"""Shared data, hashing, API contracts and atomic state IO. No account defaults."""
from __future__ import annotations
import copy, hashlib, json, os, re, time, urllib.request, urllib.error, uuid
from pathlib import Path

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, data):
    p=Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    tmp=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex)
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,p)

def resolve(base, value):
    p=Path(value)
    return p if p.is_absolute() else Path(base)/p

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def prompt(base,g):
    return resolve(base,g['prompt_path']).read_bytes().decode('utf-8-sig')

def select(project, spec=None):
    if not spec: return list(project.get('groups',[]))
    wanted=set()
    for part in spec.split(','):
        if '-' in part:
            a,b=map(int,part.split('-',1)); wanted.update(range(a,b+1))
        else: wanted.add(int(part))
    available={int(e['id']) for e in project.get('episodes',[])}
    if wanted-available: raise ValueError('Requested episodes absent: '+str(sorted(wanted-available)))
    groups=[g for g in project.get('groups',[]) if ({int(u['episode']) for u in g.get('uses',[])} or {int(g['episode'])}) & wanted]
    covered=set().union(*[({int(u['episode']) for u in g.get('uses',[])} or {int(g['episode'])}) for g in groups]) if groups else set()
    if wanted-covered:raise ValueError('Requested episodes have no prepared requests: '+str(sorted(wanted-covered)))
    return groups

def field(data,path):
    cur=data
    for part in re.sub(r'\[(\d+)\]',r'.\1',path).split('.'):
        if not part: continue
        try: cur=cur[int(part)] if isinstance(cur,list) else cur[part]
        except (KeyError,IndexError,TypeError,ValueError): return None
    return cur

def first(data,paths):
    for p in paths:
        v=field(data,p)
        if v is not None and v!='': return v
    return None

def render(value,ctx):
    if isinstance(value,str) and value.startswith('$'):
        if value[1:] not in ctx: raise ValueError('Unknown contract variable: '+value)
        return ctx[value[1:]]
    if isinstance(value,list): return [render(x,ctx) for x in value]
    if isinstance(value,dict): return {k:render(v,ctx) for k,v in value.items()}
    return copy.deepcopy(value)

def api_json(url,method='GET',body=None,key=None,timeout=180,headers=None):
    hs={'User-Agent':'muflow/0.1','Accept':'application/json'}
    if key: hs['Authorization']='Bearer '+key
    hs.update(headers or {})
    data=None
    if body is not None:
        data=json.dumps(body,ensure_ascii=False).encode(); hs['Content-Type']='application/json'
    req=urllib.request.Request(url,data=data,headers=hs,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8-sig'))

def require_contract(cfg,operation):
    c=cfg.get('contracts',{}).get(operation,{})
    if c.get('verified') is not True or not c.get('url') or not c.get('verification_evidence'):
        raise ValueError(operation+' API contract not verified/configured')
    if c.get('operation')!=operation: raise ValueError('API operation mismatch')
    return c

def api_key(cfg):
    name=cfg.get('api_key_env','DRAMA_API_KEY')
    value=os.environ.get(name)
    if not value: raise ValueError('Missing environment variable '+name)
    return value

def safe_name(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(text)).strip('. ') or 'item'

def authorization(cfg,scope):
    if cfg.get('setup',{}).get('status')!='paired':raise ValueError('Complete installation pairing and credential setup before execution')
    a=cfg.get('authorization',{})
    if not a.get('execution_enabled') or scope not in a.get('scopes',[]):
        raise ValueError('Execution scope not enabled: '+scope)
    if not a.get('evidence'): raise ValueError('Authorization evidence missing')
    if not isinstance(a.get('budget_yuan'),(int,float)) or a['budget_yuan']<=0:
        raise ValueError('Positive authorized budget required')

def issue(code,obj,actual,evidence='',status='不通过'):
    return {'id':code,'object':obj,'version':'current','status':status,'actual':actual,
            'evidence':evidence,'direct_cause':'待核对','root_cause':'待验证',
            'next_action':'定位对应源数据或产物并修复后复检'}


def contract_variables(value):
    if isinstance(value,str):return {value[1:]} if re.fullmatch(r'\$[A-Za-z_][A-Za-z0-9_]*',value) else set()
    if isinstance(value,list):return set().union(*(contract_variables(v) for v in value)) if value else set()
    if isinstance(value,dict):return set().union(*(contract_variables(v) for v in value.values())) if value else set()
    return set()

def video_contract_check(cfg,items):
    c=require_contract(cfg,'video');used=contract_variables(c.get('body',{}));fixed=c.get('fixed_parameters',{});v=cfg['video']
    for it in items:
        values={'model':v['model'],'prompt':it['prompt'],'duration':it['duration'],'ratio':it['ratio'],'resolution':v['resolution']}
        for k,value in values.items():
            if k not in used and not (k!='prompt' and fixed.get(k)==value and c.get('fixed_parameters_evidence')):
                raise ValueError('Contract does not transmit actual '+k)
        if 'images' not in used and not ('first_image' in used and len(it['images'])==1):raise ValueError('Contract does not transmit every bound reference')
    return c
