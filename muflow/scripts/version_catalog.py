"""Expose all retained iterations without declaring the newest one user-approved."""
import argparse,html,json
from pathlib import Path
from core import load,save,resolve,sha

def build(project_path,out):
    pp=Path(project_path);data=load(pp);out=Path(out)
    if out.exists():raise ValueError('Use a new catalog version')
    rows=data.get('iterations',[]);chunks=[]
    for row in rows:
        for k in ('id','label','reason','status','kind','local_path'):
            if not row.get(k):raise ValueError('Iteration missing '+k)
        p=resolve(pp.parent,row['local_path'])
        if not p.is_file():raise ValueError('Iteration file absent: '+row['id'])
        if sha(p)!=row.get('sha256'):raise ValueError('Iteration hash changed: '+row['id'])
        status=row['status']
        if status=='用户采用' and not row.get('user_approval_evidence'):raise ValueError('Agent assessment is not user approval')
        uri=p.resolve().as_uri();esc=lambda s:html.escape(str(s),quote=True)
        media=('<img src="'+esc(uri)+'" style="max-width:380px;max-height:380px">') if row['kind']=='image' else '<a href="'+esc(uri)+'">打开视频片段</a>'
        chunks.append('<article><h2>'+esc(row['label'])+'</h2>'+media+'<p>状态：'+esc(status)+'；变更/退回原因：'+esc(row['reason'])+'</p><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></article>')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text('<!doctype html><meta charset="utf-8"><title>全部尝试与采用情况</title><h1>全部尝试与采用情况</h1>'+''.join(chunks),encoding='utf-8')
    return {'iterations':len(rows),'output':str(out)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('project');p.add_argument('out');a=p.parse_args();print(json.dumps(build(a.project,a.out),ensure_ascii=False))
