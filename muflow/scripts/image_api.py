"""One image operation with audited real input and restart-safe submission state."""
from __future__ import annotations
import argparse,base64,json,mimetypes,os,time,urllib.request,urllib.parse,uuid
from pathlib import Path
from core import *
from batch_video import Store,exclusive,download,status

def multipart(url,fields,key,timeout):
    boundary='drama-'+uuid.uuid4().hex;parts=[]
    for name,value in fields.items():
        head=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if isinstance(value,dict) and '__file__' in value:
            p=Path(value['__file__']);head+=f'; filename="{p.name}"\r\nContent-Type: {mimetypes.guess_type(p.name)[0] or "application/octet-stream"}\r\n\r\n'
            parts.extend([head.encode(),p.read_bytes(),b'\r\n'])
        else:parts.extend([(head+'\r\n\r\n').encode(),(json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value)).encode(),b'\r\n'])
    parts.append(f'--{boundary}--\r\n'.encode())
    req=urllib.request.Request(url,b''.join(parts),{'Authorization':'Bearer '+key,'User-Agent':'muflow/0.1','Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def _refresh(project_path):
    from refresh_progress import refresh
    return refresh(project_path)

def perform(project_path,profile_path,aid,execute=False):
    pp=Path(project_path);base=pp.parent;project=load(pp);assets={a['id']:a for a in project['assets']};a=assets[aid]
    if a.get('mode')=='reuse':
        p=resolve(base,a['local_path'])
        if sha(p)!=a.get('sha256') or a.get('review',{}).get('status')!='通过' or not a.get('review',{}).get('evidence'):raise ValueError('Reused image requires actual hash and review')
        if project.get('schema_version')==2:
            from refresh_progress import refresh
            refresh(pp)
        return {'operation':'reuse','phase':'saved','output':str(p),'sha256':sha(p),'external_requests':0}
    cfg=load(profile_path)
    if project.get('schema_version')!=2 and not (cfg.get('video',{}).get('test_mode') is True and urllib.parse.urlsplit(cfg.get('contracts',{}).get('image_edit' if a.get('mode')=='edit' else 'image_generate',{}).get('url','')).hostname in ('127.0.0.1','localhost','::1')):raise ValueError('Migrate project and complete staged user review before production images')
    if a.get('parent_id') and a.get('mode')!='edit':raise ValueError('Reference derivatives require image editing, not text generation')
    operation='image_edit' if a.get('mode')=='edit' else 'image_generate';c=require_contract(cfg,operation)
    ctx={'model':cfg['image']['model'],'prompt':a['prompt'],'ratio':project['settings'].get('scene_ratio','16:9') if a['kind']=='scene' else project['settings'].get('asset_ratio','16:9'),'size':cfg['image']['size']}
    audit={'operation':operation,'endpoint':c['url'],'method':c.get('method','POST'),'asset_id':aid,'asset_version':a.get('version','v1')}
    if operation=='image_edit':
        parent=assets.get(a.get('parent_id'))
        if not parent:raise ValueError('Mother requires actual reviewed image')
        if project.get('schema_version')==2:
            from review_state import mother_ready
            mother_ready(project,base,parent)
        elif parent.get('review',{}).get('status')!='通过' or not parent.get('review',{}).get('evidence'):raise ValueError('Mother requires actual reviewed image')
        src=resolve(base,parent['local_path']);h=sha(src)
        if h!=parent.get('sha256'):raise ValueError('Mother changed')
        ctx.update(input_file={'__file__':str(src)},input_url=parent.get('remote_url'),input_b64=base64.b64encode(src.read_bytes()).decode())
        used=[x for x in ('input_file','input_url','input_b64') if x in contract_variables(c['body'])]
        if not used or any(not ctx[x] for x in used):raise ValueError('Edit contract must actually pass the original image')
        if c.get('encoding','json')=='json' and 'input_file' in used:raise ValueError('File inputs require multipart encoding')
        audit.update(input_asset_id=parent['id'],input_sha256=h,input_variables=used,input_count=1)
    if not c.get('verification_evidence'):raise ValueError('Documented edit/generate contract evidence required')
    if execute and project.get('schema_version')==2:
        from workflow_gate import image_generation
        expected=image_generation(project,base,a,cfg)
        if 'prompt' not in contract_variables(c['body']):raise ValueError('Image contract does not transmit the approved prompt')
        if 'model' not in contract_variables(c['body']) and not (c.get('fixed_parameters',{}).get('model')==ctx['model'] and c.get('fixed_parameters_evidence')):raise ValueError('Image contract does not transmit the selected model')
        if ctx['ratio']!=expected:raise ValueError('Actual image ratio differs from confirmed asset ratio')
        if 'ratio' not in contract_variables(c['body']) and not (c.get('fixed_parameters',{}).get('ratio')==expected and c.get('fixed_parameters_evidence')):raise ValueError('Image contract does not transmit the confirmed asset ratio')
    body=render(c['body'],ctx)
    if not execute:return {'operation':operation,'audit':audit,'body_fields':list(body),'mode':'dry-run'}
    authorization(cfg,'images')
    if project.get('schema_version')==2:
        if cfg['authorization'].get('project_scope',{}).get('project_id')!=project['project_id']:raise ValueError('Image authorization belongs to a different project')
        if a.get('revision_kind')=='content_redo' and cfg['authorization'].get('allow_content_redo') is not True:raise ValueError('Paid content redo requires its own scope')
    key=api_key(cfg);out=base/'99_工作区'/'生图原始记录';out.mkdir(parents=True,exist_ok=True)
    store=Store(base/'production.sqlite');sk='image:'+aid;fp=digest({'prompt':a['prompt'],'audit':audit,'model':ctx['model']})
    with exclusive(out/(safe_name(aid)+'.lock')):
        old=store.get(sk)
        if old:
            with store.db() as conn:
                row=conn.execute('SELECT fingerprint FROM jobs WHERE id=?',(sk,)).fetchone()
            if row and row[0]!=fp:raise ValueError('Image task changed; use a new asset version ID')
        if old and old.get('phase')=='saved':
            if project.get('schema_version')==2:
                from asset_gallery import register_saved
                register_saved(pp,aid,old)
                from refresh_progress import refresh
                refresh(pp)
            return old
        if old and old.get('phase') in ('submitting','uncertain'):raise ValueError('Previous image POST uncertain; reconcile before retry')
        if old and old.get('phase')=='failed':raise ValueError('Create a new image asset version for retry and preserve evidence')
        price=cfg['image'].get('max_price_per_attempt_yuan')
        if not isinstance(price,(int,float)) or price<=0:raise ValueError('Image price cap required')
        d=old or {'phase':'ready','audit':audit}
        if not old:
            store.reserve(sk,price,cfg['authorization']['budget_yuan']);d['phase']='submitting';store.put(sk,fp,d);_refresh(pp)
            save(out/(safe_name(aid)+'_request_audit.json'),{'audit':audit,'body_keys':list(body),'prompt':a['prompt']})
            try:resp=multipart(c['url'],body,key,c.get('timeout_seconds',240)) if c.get('encoding')=='multipart' else api_json(c['url'],c.get('method','POST'),body,key,c.get('timeout_seconds',240))
            except Exception:
                d['phase']='uncertain';store.put(sk,fp,d);_refresh(pp);raise
            d['response']=resp;store.put(sk,fp,d);_refresh(pp)
        else:resp=d.get('response',{})
        started=time.time()
        while True:
            url=first(resp,c.get('result_url_paths',[]));b64=first(resp,c.get('result_b64_paths',[]));tid=first(resp,c.get('id_paths',[])) or d.get('task_id')
            if url or b64:break
            if status(resp,c)=='failed':d.update(phase='failed',response=resp);store.put(sk,fp,d);_refresh(pp);return d
            if not tid or not c.get('poll_url'):
                d.update(phase='uncertain',response=resp);store.put(sk,fp,d);_refresh(pp);raise ValueError('Image result shape unrecognized; keep original task evidence')
            d.update(phase='running',task_id=str(tid),response=resp);store.put(sk,fp,d);_refresh(pp)
            if time.time()-started>=c.get('poll_window_seconds',240):return d
            time.sleep(c.get('poll_seconds',10));resp=api_json(c['poll_url'].replace('{task_id}',urllib.parse.quote(str(tid),safe='')),key=key)
        dest=out/(safe_name(aid)+'.image')
        if dest.exists():raise ValueError('Image output exists; use a new asset version')
        if b64:dest.write_bytes(base64.b64decode(b64,validate=True))
        else:
            try:download(url,dest)
            except Exception:
                d.update(phase='result_ready',response=resp);store.put(sk,fp,d);_refresh(pp);raise
        from PIL import Image
        with Image.open(dest) as im:im.verify()
        head=dest.read_bytes()[:16]
        suffix='.png' if head.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if head.startswith(b'\xff\xd8') else '.webp' if head.startswith(b'RIFF') and head[8:12]==b'WEBP' else '.img'
        actual=dest.with_suffix(suffix)
        if actual.exists():raise ValueError('Output version already exists')
        os.replace(dest,actual);dest=actual
        d.update(phase='saved',output=str(dest),sha256=sha(dest),audit=audit,response=resp,review_status='待复核')
        save(out/(safe_name(aid)+'_result_audit.json'),d);store.put(sk,fp,d);_refresh(pp)
        if project.get('schema_version')==2:
            from asset_gallery import register_saved
            register_saved(pp,aid,d)
            from refresh_progress import refresh
            refresh(pp)
        return d
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('project');p.add_argument('profile');p.add_argument('asset_id');p.add_argument('--execute',action='store_true');a=p.parse_args()
    result=perform(a.project,a.profile,a.asset_id,a.execute)
    print(json.dumps({k:v for k,v in result.items() if k not in ('response',)},ensure_ascii=False,indent=2))
