"""Resumable multi-episode pool. Dry-run by default. Live protocols must be verified."""
from __future__ import annotations
import argparse, concurrent.futures as cf, contextlib, json, os, re, sqlite3, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from decimal import Decimal
from core import *
from build_preview import compile_jobs
from validate_project import validate

class Store:
    def __init__(self,path):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT, data TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, units INTEGER)')
    @contextlib.contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=30)
        try:
            with conn:yield conn
        finally:conn.close()
    def get(self,k):
        with self.db() as c:r=c.execute('SELECT data FROM jobs WHERE id=?',(k,)).fetchone()
        return json.loads(r[0]) if r else None
    def put(self,k,fingerprint,d):
        with self.db() as c:
            old=c.execute('SELECT fingerprint FROM jobs WHERE id=?',(k,)).fetchone()
            if old and old[0]!=fingerprint:raise ValueError('Task changed; use a new group/version ID: '+k)
            c.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)',(k,fingerprint,json.dumps(d,ensure_ascii=False)))
    def reserve(self,k,amount,budget):
        units=int(Decimal(str(amount))*1000000);limit=int(Decimal(str(budget))*1000000)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM reservations WHERE id=?',(k,)).fetchone():return
            used=c.execute('SELECT COALESCE(SUM(units),0) FROM reservations').fetchone()[0]
            if used+units>limit:raise ValueError('Authorized budget exhausted; keep pending tasks')
            c.execute('INSERT INTO reservations VALUES (?,?)',(k,units))
    def record_cost(self,k,amount):
        try:units=int(Decimal(str(amount))*1000000)
        except Exception:return
        with self.db() as c:c.execute('UPDATE reservations SET units=MAX(units,?) WHERE id=?',(units,k))
    def record_billing(self,k,kind,amount,evidence):
        units=int(Decimal(str(amount))*1000000)
        if units<0 or kind not in ('reported_charge','reported_refund'):raise ValueError('Invalid billing event')
        event=digest({'attempt':k,'kind':kind,'units':units,'evidence':evidence})
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS billing_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT UNIQUE,attempt TEXT,kind TEXT,units INTEGER,evidence TEXT)')
            c.execute('INSERT OR IGNORE INTO billing_events(event,attempt,kind,units,evidence) VALUES (?,?,?,?,?)',(event,k,kind,units,evidence))
    def billing(self):
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS billing_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT UNIQUE,attempt TEXT,kind TEXT,units INTEGER,evidence TEXT)')
            rows=c.execute('SELECT attempt,kind,units,evidence FROM billing_events ORDER BY seq').fetchall()
            attempts=[r[0] for r in c.execute('SELECT id FROM reservations')]
        latest={}
        for a,k,u,e in rows:latest[(a,k)]=(u,e)
        charged=sum(u for (a,k),(u,e) in latest.items() if k=='reported_charge')/1000000
        refunded=sum(u for (a,k),(u,e) in latest.items() if k=='reported_refund')/1000000
        return {'reserved_upper_bound_yuan':self.reserved(),'reported_charge_yuan':charged,'reported_refund_yuan':refunded,
                'reported_net_yuan':charged-refunded,'unknown_charge_attempts':[a for a in attempts if (a,'reported_charge') not in latest],
                'note':'Provider-reported amounts only. Reservations are not actual spending; refunds remain unknown without explicit receipts.'}
    def reserved(self):
        with self.db() as c:return c.execute('SELECT COALESCE(SUM(units),0) FROM reservations').fetchone()[0]/1000000

@contextlib.contextmanager
def exclusive(path):
    f=open(path,'a+b');f.seek(0);f.write(b'0');f.flush();f.seek(0)
    try:
        if os.name=='nt':
            import msvcrt;msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl;fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close();raise ValueError('Another runner owns this project; do not submit twice')
    try:yield
    finally:
        f.seek(0)
        if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
        f.close()

from media_integrity import download

def status(response,c):
    val=str(first(response,c.get('status_paths',['status'])) or '').lower()
    if val in [str(x).lower() for x in c.get('success_values',['completed','success','succeeded'])]:return 'success'
    if val in [str(x).lower() for x in c.get('failure_values',['failed','failure'])]:return 'failed'
    return 'running'

def round_robin(items):
    episodes={}
    for it in items:episodes.setdefault(int(it['episode']),[]).append(it)
    out=[]
    while any(episodes.values()):
        for ep in sorted(episodes):
            if episodes[ep]:out.append(episodes[ep].pop(0))
    return out

def fingerprint(it,cfg):
    return digest({'id':it['id'],'prompt':it['prompt'],'images':[(x['asset_id'],x['sha256']) for x in it['images']],
                   'model':cfg['video']['model'],'duration':it['duration'],'ratio':it['ratio']})

def run(items,cfg,out,store,max_seconds=None,grant=None):
    from execution_gate import enforce
    local_test=cfg.get('video',{}).get('test_mode') is True and urllib.parse.urlsplit(cfg.get('contracts',{}).get('video',{}).get('url','')).hostname in ('127.0.0.1','localhost','::1') and all(it.get('schema_version',1)==1 for it in items)
    if not local_test:enforce(items,cfg,grant)
    contract=video_contract_check(cfg,items);key=api_key(cfg);video=cfg['video'];cap=int(video['concurrency']);poll=float(video.get('poll_seconds',180))
    if not 1<=cap<=int(video['verified_concurrency_limit']):raise ValueError('Concurrency outside verified limit')
    price=video.get('max_price_per_attempt_yuan')
    if not isinstance(price,(int,float)) or price<=0:raise ValueError('Positive verified price cap required')
    order=round_robin(items);seq={it['id']:i for i,it in enumerate(order)};byid={it['id']:it for it in items}
    fps={it['id']:fingerprint(it,cfg) for it in items}
    for it in items:
        d=store.get(it['id'])
        if d is None:d={'phase':'ready','attempts':0,'history':[],'next_at':0}
        elif d['phase']=='submitting':d.update(phase='uncertain',error='Interrupted during POST; reconcile original request first')
        elif d['phase']=='blocked':d['phase']=d.pop('resume_phase','ready')
        if d.get('phase')=='downloaded':
            try:
                from media_integrity import check_mp4
                file=Path(d['output']);check_mp4(file)
                if sha(file)!=d.get('sha256'):
                    d['verify_expected_sha256']=d.get('sha256');raise ValueError('Saved output hash changed; restore original result')
            except Exception as e:
                d.update(phase='poll_result' if d.get('task_id') else 'download' if d.get('result_url') else 'uncertain',error='Saved file requires recovery: '+str(e))
        store.put(it['id'],fps[it['id']],d)
    started=time.time();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    def step(jid):
        it=byid[jid];fp=fps[jid];d=store.get(jid);phase=d['phase'];now=time.time()
        try:
            if phase in ('ready','retry'):
                for image in it['images']:
                    if sha(image['path'])!=image.get('sha256'):raise ValueError('Actual reference changed since preview: '+image['asset_id'])
                    if not image.get('url') or not image['url'].startswith(('https://','http://')):raise ValueError('Reference not uploaded: '+image['asset_id'])
                n=d['attempts']+1;client=digest({'task':jid,'fp':fp,'attempt':n})
                store.reserve(jid+':'+str(n),price,cfg['authorization']['budget_yuan'])
                [d.pop(k,None) for k in ('task_id','result_url','output')];d.update(phase='submitting',attempts=n,client_key=client);d['history'].append({'attempt':n,'client_key':client,'reserved_yuan':price,'started':now})
                store.put(jid,fp,d)
                ctx={'model':video['model'],'prompt':it['prompt'],'images':[x['url'] for x in it['images']],
                     'first_image':it['images'][0]['url'] if it['images'] else None,'duration':it['duration'],'ratio':it['ratio'],
                     'resolution':video['resolution'],'client_key':client}
                payload=render(contract['body'],ctx)
                save(out/(safe_name(jid)+f'_attempt{n}_request.json'),{'operation':'video','endpoint':contract['url'],'payload':payload,'asset_hashes':[x['sha256'] for x in it['images']]})
                try:response=api_json(contract['url'],contract.get('method','POST'),payload,key,contract.get('timeout_seconds',240))
                except Exception as e:
                    d.update(phase='uncertain',error='POST result unknown: '+type(e).__name__);store.put(jid,fp,d);return
            elif phase in ('running','poll_result'):
                url=contract['poll_url'].replace('{task_id}',urllib.parse.quote(str(d['task_id']),safe=''))
                try:response=api_json(url,key=key,timeout=contract.get('poll_timeout_seconds',60))
                except Exception as e:
                    d.update(next_at=now+poll,error='Poll interrupted: '+type(e).__name__);store.put(jid,fp,d);return
            elif phase=='download':
                dest=out/('第%02d集_'%it['episode']+safe_name(jid)+f'_attempt{d["attempts"]}.mp4')
                try:download(d['result_url'],dest,expected_sha256=d.get('verify_expected_sha256'))
                except Exception as e:
                    d.update(next_at=now+max(1,poll),error='Download pending: '+type(e).__name__)
                    if d.get('task_id'):d['phase']='poll_result'
                    store.put(jid,fp,d);return
                d.update(phase='downloaded',output=str(dest),sha256=sha(dest),bytes=dest.stat().st_size,error='')
                store.put(jid,fp,d);return
            else:return
            receipt=out/(safe_name(jid)+f'_attempt{d["attempts"]}_receipt_'+str(time.time_ns())+'_'+uuid.uuid4().hex[:8]+'.json')
            save(receipt,response)
            d['history'][-1].setdefault('receipts',[]).append(str(receipt))
            store.put(jid,fp,d)
            tid=first(response,contract.get('id_paths',['id','task_id','data.id','data.task_id']))
            if tid:d['task_id']=str(tid);d['history'][-1]['task_id']=str(tid)
            st=status(response,contract);d['history'][-1]['last_state']=st
            refund=first(response,contract.get('refund_paths',[]))
            if refund is not None:store.record_billing(jid+':'+str(d['attempts']),'reported_refund',refund,str(receipt))
            cost=first(response,contract.get('cost_paths',[]))
            if cost is not None:
                d['history'][-1]['reported_cost_yuan']=cost;store.record_cost(jid+':'+str(d['attempts']),cost);store.record_billing(jid+':'+str(d['attempts']),'reported_charge',cost,str(receipt))
                if Decimal(str(cost))>Decimal(str(price)):d['price_exceeded']=True
            if st=='failed':
                d.update(phase='retry',next_at=now+float(video.get('retry_seconds',30)),error=str(first(response,contract.get('error_paths',['error','message'])) or 'Provider explicitly failed'))
            elif st=='success':
                url=first(response,contract.get('result_url_paths',['video_url','data.video_url']))
                if url:d.update(phase='download',result_url=url,next_at=now,error='')
                elif d.get('task_id'):d.update(phase='poll_result',next_at=now+poll,error='Success but result URL pending; query same task')
                else:d.update(phase='uncertain',error='Success response without result URL/task ID')
            elif d.get('task_id'):d.update(phase='running',next_at=now+poll,error='')
            else:d.update(phase='uncertain',error='POST returned no task ID or terminal state')
        except Exception as e:
            if phase in ('ready','retry') and d.get('phase') not in ('ready','retry'):
                d.update(phase='running' if d.get('task_id') else 'uncertain',error=type(e).__name__,next_at=now+poll)
            else:d.update(phase='blocked',resume_phase=phase,error=str(e))
        store.put(jid,fp,d)
    pool=cf.ThreadPoolExecutor(max_workers=cap+2);futures={}
    try:
        while True:
            states={k:store.get(k) for k in byid}
            for fut,k in list(futures.items()):
                if fut.done():fut.result();del futures[fut]
            states={k:store.get(k) for k in byid}
            in_flight=set(futures.values());cloud=sum(d['phase'] in ('running','submitting','uncertain','poll_result') or (k in in_flight and d['phase'] in ('ready','retry')) for k,d in states.items())
            candidates=sorted(byid,key=lambda k:(states[k]['attempts'],seq[k]))
            for k in candidates:
                d=store.get(k)
                if k in in_flight or d.get('next_at',0)>time.time():continue
                p=d['phase']
                if p in ('ready','retry'):
                    if cloud>=cap:continue
                    if any(x.get('price_exceeded') for x in states.values()):
                        d.update(phase='blocked',resume_phase=p,error='Provider reported cost exceeds approved attempt cap');store.put(k,fps[k],d);continue
                    cloud+=1
                elif p not in ('running','poll_result','download'):continue
                if len(futures)>=cap+2:break
                # Mark ownership in futures before scheduling another cycle.
                futures[pool.submit(step,k)]=k
            current={k:store.get(k) for k in byid}
            tasks=[{'id':k,'episode':byid[k]['episode'],**d} for k,d in current.items()]
            save(out/'batch_summary.json',{'tasks':tasks,'reserved_cost_upper_bound_yuan':store.reserved(),'billing':store.billing(),'billing_note':'Reservations are conservative. Reported platform costs in history; unknown charges are not assumed zero.'})
            if not futures and all(d['phase'] in ('downloaded','blocked','uncertain') for d in current.values()):break
            if not futures and any(d.get('price_exceeded') for d in current.values()) and not any(d['phase'] in ('running','poll_result','download') for d in current.values()):break
            if max_seconds is not None and time.time()-started>=max_seconds and not futures:break
            time.sleep(0.05 if video.get('test_mode') else 1)
    finally:pool.shutdown(wait=True)
    return load(out/'batch_summary.json')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('profile');ap.add_argument('preview');ap.add_argument('--episodes');ap.add_argument('--out',required=True);ap.add_argument('--execute',action='store_true');ap.add_argument('--max-seconds',type=float);a=ap.parse_args()
    pp=Path(a.project);data=load(pp);cfg=load(a.profile)
    from execution_gate import prepare
    items,grant=prepare(pp,cfg,a.preview,a.episodes)
    print(json.dumps({'mode':'execute' if a.execute else 'dry-run','groups':len(items),'episodes':sorted({i['episode'] for i in items}),'external_requests':0 if not a.execute else 'authorized'},ensure_ascii=False))
    if a.execute:
        out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
        with exclusive(pp.parent/'production-runner.lock'):
            store=Store(pp.parent/'production.sqlite');run(items,cfg,out,store,a.max_seconds,grant)
