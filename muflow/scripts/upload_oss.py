"""Optional OSS transport; private project objects, temporary URLs, env-only secrets."""
import argparse,json,os
from pathlib import Path
from core import *

def publish(project_path,profile_path,execute=False):
    pp=Path(project_path);p=load(pp);cfg=load(profile_path);oss=cfg.get('oss',{})
    if not oss.get('verified') or not all(oss.get(k) for k in ('endpoint','bucket','prefix')):raise ValueError('Configure verified OSS project prefix')
    prefix=oss['prefix'].strip('/')
    if '..' in prefix.split('/') or not prefix:raise ValueError('Dedicated nonempty project prefix required')
    manifest=[]
    if execute:
        authorization(cfg,'upload')
        if p.get('schema_version')==2 and cfg['authorization'].get('project_scope',{}).get('project_id')!=p['project_id']:raise ValueError('Upload authorization belongs to a different project')
        import oss2
        auth=oss2.Auth(os.environ[oss.get('access_key_id_env','DRAMA_OSS_ACCESS_KEY_ID')],os.environ[oss.get('access_key_secret_env','DRAMA_OSS_ACCESS_KEY_SECRET')])
        bucket=oss2.Bucket(auth,oss['endpoint'],oss['bucket'])
    for a in p.get('assets',[]):
        if not a.get('local_path') or a.get('review',{}).get('status')!='通过':continue
        if not a.get('review',{}).get('evidence'):raise ValueError('Actual review evidence missing: '+a['id'])
        file=resolve(pp.parent,a['local_path']);h=sha(file)
        if h!=a.get('sha256'):raise ValueError('Image hash changed: '+a['id'])
        key=prefix+'/'+h+file.suffix.lower();manifest.append({'asset_id':a['id'],'object':key,'sha256':h})
        if execute:
            if not bucket.object_exists(key):bucket.put_object_from_file(key,str(file),headers={'x-oss-object-acl':'private'})
            a['remote_sha256']=h;a['remote_reference_evidence']='私有对象由本次同哈希本地图上传或核对复用，object='+key
            a['remote_url']=bucket.sign_url('GET',key,int(oss.get('url_lifetime_seconds',172800)));a['remote_object']=key
    if execute:save(pp,p)
    return {'mode':'execute' if execute else 'dry-run','objects':manifest}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('profile');ap.add_argument('--execute',action='store_true');a=ap.parse_args();print(json.dumps(publish(a.project,a.profile,a.execute),ensure_ascii=False))
