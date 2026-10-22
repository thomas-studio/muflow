"""Validate installer-supplied pairing; keep all concrete provider data outside the skill."""
from __future__ import annotations
import argparse,copy,json,os
from pathlib import Path
from urllib.parse import urlsplit
from core import load,save,digest,contract_variables

def pair(config,output,replace=False):
    target=Path(output).resolve();skill=Path(__file__).resolve().parents[1]
    if target==skill or skill in target.parents:raise ValueError('Pairing profile must be outside the skill folder')
    if target.exists() and not replace:raise ValueError('Profile exists; use --replace for an intentional local update')
    cfg=copy.deepcopy(config);problems=[]
    def required(value,field):
        if value is None or value=='' or value==[] or value=={}:problems.append(field+' is required')
    def endpoint(value,field):
        parsed=urlsplit(str(value or ''))
        if parsed.scheme not in ('https','http') or not parsed.netloc:problems.append(field+' must be a user-provided HTTP(S) URL')
    def no_credentials(value,path=''):
        if isinstance(value,dict):
            for k,v in value.items():
                full=path+'.'+k
                if k.lower() in ('api_key','access_key_id','access_key_secret','secret_key','access_token','authorization_header') and v:problems.append(full+' must use a credential reference, not a secret value')
                no_credentials(v,full)
        elif isinstance(value,list):
            for i,v in enumerate(value):no_credentials(v,path+'.'+str(i))
    no_credentials(cfg)
    required(cfg.get('api_key_env'),'api_key_env')
    for section in ('image','video'):
        block=cfg.get(section,{})
        required(block.get('model'),section+'.model')
        price=block.get('max_price_per_attempt_yuan')
        if type(price) not in (int,float) or price<=0:problems.append(section+'.max_price_per_attempt_yuan must be the user-verified positive quote cap')
    vid=cfg.get('video',{})
    for field in ('resolution','concurrency','verified_concurrency_limit','verified_max_images','verified_durations_seconds'):required(vid.get(field),'video.'+field)
    for operation in ('image_generate','image_edit','video'):
        c=cfg.get('contracts',{}).get(operation,{})
        if c.get('operation')!=operation:problems.append(operation+' operation mismatch')
        endpoint(c.get('url'),operation+'.url');required(c.get('body'),operation+'.body')
        if c.get('verified') is not True or not c.get('verification_evidence'):problems.append(operation+' needs actual protocol verification evidence')
        if c.get('poll_url'):endpoint(c['poll_url'].replace('{task_id}','test'),operation+'.poll_url')
    body=contract_variables(cfg.get('contracts',{}).get('image_edit',{}).get('body',{}))
    if not any(s in body for s in ('input_file','input_url','input_b64')):problems.append('image_edit must transmit the original image')
    oss=cfg.get('oss')
    if oss:
        endpoint(oss.get('endpoint'),'oss.endpoint')
        for field in ('bucket','prefix','access_key_id_env','access_key_secret_env'):required(oss.get(field),'oss.'+field)
        if oss.get('verified') is not True:problems.append('OSS settings need user verification')
    if problems:raise ValueError('\n'.join(problems))
    a=cfg.setdefault('authorization',{});a.setdefault('execution_enabled',False);a.setdefault('scopes',[]);a.setdefault('budget_yuan',None);a.setdefault('evidence',None)
    configured_envs=[cfg['api_key_env']]
    if oss:configured_envs += [oss['access_key_id_env'],oss['access_key_secret_env']]
    missing=[n for n in configured_envs if not os.environ.get(n)]
    cfg['setup']={'status':'pending_credentials' if missing else 'paired','missing_credential_refs':missing,
                  'local_only':True,'protocol_digest':digest(cfg.get('contracts',{}))}
    save(target,cfg)
    return {'profile_written':str(target),'status':cfg['setup']['status'],'missing_credential_refs':missing,'external_requests':0,'mode':'configuration_only','video_probe_authorized':False}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--replace',action='store_true');a=p.parse_args()
    print(json.dumps(pair(load(a.input),a.out,a.replace),ensure_ascii=False))
