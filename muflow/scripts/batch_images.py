"""Resume all authorised image assets with a configurable target, never a hard ceiling."""
from __future__ import annotations
import argparse,concurrent.futures as cf,json,time
from pathlib import Path
from core import load,authorization
from batch_video import Store,exclusive
from image_api import perform
from stage_queue import next_batch,start_batch,complete_image,block_image,QUEUE

_CONFIG=object()

def run(project_path,profile_path,execute=False,concurrency=_CONFIG,max_seconds=None):
    pp=Path(project_path);base=pp.parent;cfg=load(profile_path);p=load(pp)
    target=cfg.get('image',{}).get('concurrency',10) if concurrency is _CONFIG else concurrency
    if target is not None and (type(target) is not int or target<1):raise ValueError('Concurrency must be positive or null for no limit')
    if not execute:return {'mode':'dry-run','image_concurrency_target':target,'no_hard_upper_limit':True,'external_requests':0}
    authorization(cfg,'images')
    if p.get('schema_version')!=2:raise ValueError('Actual execution requires staged workflow project')
    from workflow_gate import confirmed,text_ready
    from audit_project_network import require_clean
    confirmed(p);text_ready(p,base);require_clean(base)
    if cfg.get('setup',{}).get('status')!='paired':raise ValueError('API pairing incomplete')
    if cfg['authorization'].get('project_scope',{}).get('project_id')!=p['project_id']:raise ValueError('Image authorization belongs to a different project')
    started=time.monotonic();store=Store(base/'production.sqlite');waiting={};futures={};errors={}
    total=len([a for a in p.get('assets',[]) if a.get('mode')!='reuse'])
    workers=max(1,total) if target is None else target
    # The only finite worker target is user-selected/default 10; null submits the entire eligible set.
    with exclusive(base/'.image-runner.lock'),cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            # Restore in-progress queue items on restart; image_api reconciles prior saved/uncertain POSTs.
            q=load(base/QUEUE) if (base/QUEUE).is_file() else None
            if q:
                for b in q['stages']['images']['batches']:
                    if b['status']=='running':
                        for aid in b['asset_ids']:
                            item=q['stages']['images']['items'][aid]
                            if item['status']=='running' and aid not in waiting and aid not in futures.values():waiting[aid]={'batch':b['id'],'next_at':0}
            for future,aid in list(futures.items()):
                if not future.done():continue
                del futures[future];entry=waiting[aid];bid=entry['batch']
                try:
                    result=future.result();phase=result.get('phase')
                    if phase=='saved':
                        artifact={'path':result['output'],'sha256':result['sha256'],'kind':'image','asset_id':aid}
                        complete_image(pp,bid,aid,artifact,'实际图片已保存、登记且哈希一致；不代表视觉验收')
                        del waiting[aid]
                    elif phase in ('retry','failed','running','result_ready'):
                        entry['next_at']=max(time.time()+0.05,float(result.get('next_at',0)))
                    else:raise ValueError('Original image task requires reconciliation: '+str(phase))
                except Exception as exc:
                    state=store.get('image:'+aid) or {};phase=state.get('phase')
                    # Saved bytes are reused for bounded local registration recovery, never reposted.
                    if phase=='saved' and entry.get('registration_tries',0)<3:
                        entry['registration_tries']=entry.get('registration_tries',0)+1;entry['next_at']=time.time()+0.2;continue
                    reason=('原图已保存，登记/队列需恢复：' if phase=='saved' else '原任务需核对：' if phase in ('submitting','uncertain') else '图片任务阻塞：')+type(exc).__name__+': '+str(exc)
                    block_image(pp,bid,aid,reason);errors[aid]=reason;del waiting[aid]
            # A finished mother may unlock its child while unrelated siblings remain running.
            while True:
                nxt=next_batch(pp)
                if nxt.get('stage')!='images' or nxt.get('action')!='ready':break
                batch=start_batch(pp,nxt['batch_id'])
                for aid in batch['asset_ids']:
                    if aid not in waiting and aid not in futures.values():waiting[aid]={'batch':batch['id'],'next_at':0}
            active=set(futures.values())
            # Stop only new paid submissions after an evidenced price-cap breach;
            # already running tasks continue to be recovered and recorded.
            with store.db() as conn:
                states=[json.loads(row[0]) for row in conn.execute("SELECT data FROM jobs WHERE id LIKE 'image:%'")]
                approved=cfg['image'].get('max_price_per_attempt_yuan')
                price_exceeded=any(st.get('price_exceeded') and (st.get('reported_cost_yuan') is None or float(st['reported_cost_yuan'])>float(approved)) for st in states)
            if price_exceeded:
                for aid,entry in list(waiting.items()):
                    state=store.get('image:'+aid) or {}
                    if aid not in active and state.get('phase') not in ('running','result_ready','saved'):
                        reason='平台报告费用超过已核验单次预留；暂停新增请求并核对报价'
                        block_image(pp,entry['batch'],aid,reason);errors[aid]=reason;del waiting[aid]
            for aid,entry in list(waiting.items()):
                if aid in active or entry['next_at']>time.time():continue
                if target is not None and len(futures)>=target:break
                futures[pool.submit(perform,pp,profile_path,aid,True)]=aid
            if not futures and not waiting:break
            if max_seconds is not None and time.monotonic()-started>=max_seconds and not futures:break
            time.sleep(0.05)
    return {'mode':'execute','image_concurrency_target':target,'no_hard_upper_limit':True,'next':next_batch(pp),'blocked':errors}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('profile');ap.add_argument('--execute',action='store_true');g=ap.add_mutually_exclusive_group();g.add_argument('--concurrency',type=int);g.add_argument('--unbounded',action='store_true');ap.add_argument('--max-seconds',type=float);a=ap.parse_args()
    c=None if a.unbounded else a.concurrency if a.concurrency is not None else _CONFIG
    print(json.dumps(run(a.project,a.profile,a.execute,c,a.max_seconds),ensure_ascii=False))
