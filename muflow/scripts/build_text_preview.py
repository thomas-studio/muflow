"""Legacy text-review command, now refreshing the shared run-preview entry."""
import argparse,html,json,os
from urllib.parse import quote
from pathlib import Path
from core import load,save,digest
from workflow_gate import text_manifest
from validate_project import validate
from timing_plan import render_summary
from build_preview import build as build_run_preview
from preview_ui import PANEL,details

def build(project_path,out):
    pp=Path(project_path);p=load(pp);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    fixed=pp.parent/'07_跑前预览'
    panel=build_run_preview(project_path,fixed)
    report={'issues':[]}
    try:report=validate(p,pp.parent,stage='text')
    except Exception as e:report['issues'].append({'id':'TEXT_VALIDATION','actual':str(e)})
    try:
        manifest=text_manifest(p,pp.parent);manifest_digest=digest(manifest);complete=True
    except Exception as e:
        # Draft content remains reviewable. Never manufacture an approvable
        # complete manifest when independent text-stage checks are incomplete.
        report['issues'].append({'id':'TEXT_MANIFEST_PENDING','actual':str(e)})
        manifest={'project_id':p.get('project_id'),'settings':p.get('settings',{}),
                  'assets':p.get('assets',[]),'requests':[],
                  'deliverables':{k:{'path':v,'sha256':None} for k,v in p.get('deliverables',{}).items()},
                  'input_assets':p.get('input_assets',[])}
        manifest_digest=None;complete=False
    snapshot={'manifest':manifest,'manifest_digest':manifest_digest,'issues':report['issues'],
              'media_calls':0,'phase':'awaiting_user_text_review' if complete else 'text_preparing',
              'complete':complete,'panel':panel['panel']}
    save(out/'text_review_snapshot.json',snapshot);esc=lambda s:html.escape(str(s));chunks=[]
    for key,file in manifest['deliverables'].items():chunks.append('<li>'+esc(key)+'：'+esc(file['path'])+' · '+esc(file['sha256'])+'</li>')
    body='<section class="panel"><h2>文字审阅快照</h2><p>本次刷新只保存本地审阅资料，媒体调用0次。当前内容'+('可进入文字确认检查' if complete else '仍在准备中；缺项见诊断')+'。</p>'
    body+=details('已有身份基准','<pre>'+esc(json.dumps(manifest['input_assets'],ensure_ascii=False,indent=2))+'</pre>')
    # Exact request drafts are already folded beside their single five-column
    # shot table. Do not create another long copy of the same storyboard DOM.
    body+=details('文字自检','<pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre>')+'</section>'
    file=Path(panel['panel']);doc=file.read_text(encoding='utf-8');doc=doc.replace('</main>',body+'</main>',1);file.write_text(doc,encoding='utf-8')
    link=quote(Path(os.path.relpath(file,out)).as_posix())
    (out/'全剧文字与提示词审阅.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>文字审阅入口已合并</title><p>文字、分镜和执行核对共用同一跑前入口：<a href="'+link+'#text-deliverables">打开跑前预览 · 文字与提示词审阅</a></p></html>',encoding='utf-8')
    return snapshot

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('out');a=ap.parse_args();r=build(a.project,a.out);print(json.dumps({'manifest_digest':r['manifest_digest'],'issues':len(r['issues']),'media_calls':0},ensure_ascii=False))
