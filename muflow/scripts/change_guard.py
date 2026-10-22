"""Local change gate. Keep credentials and proof records outside the skill."""
import argparse,hashlib,hmac,json,os,re
from pathlib import Path
from core import load,save,digest

def authorize(request,code,policy=None):
    if policy is None:policy=load(Path(__file__).resolve().parents[1]/'policy/change-policy.json')
    required=('evidence','target_version','change_plan_sha256','scope')
    if request.get('fully_authorized') is not True or any(not request.get(k) for k in required):
        raise ValueError('本次技能修改尚未获得完整授权。')
    if not re.fullmatch(r'[a-fA-F0-9]{64}',str(request.get('change_plan_sha256',''))):raise ValueError('本次变更方案尚未锁定。')
    if request['scope']!='skill_modification':raise ValueError('授权范围不匹配。')
    actual=hashlib.pbkdf2_hmac('sha256',str(code or '').encode(),policy['salt'].encode(),int(policy['iterations'])).hex()
    if not hmac.compare_digest(actual,policy['verifier']):raise ValueError('本次技能修改尚未通过授权核验。')
    return {k:request[k] for k in required}|{'fully_authorized':True,'verified':True,'request_digest':digest(request)}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='核验本次技能修改授权');ap.add_argument('request');ap.add_argument('--code-env',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    target=Path(a.out).resolve();skill=Path(__file__).resolve().parents[1]
    if skill==target or skill in target.parents:raise ValueError('记录须置于使用者本机工作区。')
    save(target,authorize(load(a.request),os.environ.get(a.code_env)))
    print('本次授权核验完成。')
