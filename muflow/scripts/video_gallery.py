"""Three-entrance video status; stable HTML, local-only data and on-demand playback."""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, sqlite3, urllib.parse, uuid
from pathlib import Path
from core import load, save, resolve, digest

PANEL='已出成片预览.html'
DATA='成片进度.js'
MANIFEST='成片清单.json'
CLIENT='video-preview.js'

def write_changed(path, content):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    raw=content.encode('utf-8') if isinstance(content,str) else content
    if path.exists() and path.read_bytes()==raw:return False
    temp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    temp.write_bytes(raw);os.replace(temp,path);return True

def read_tasks(base, summary=None):
    if summary is not None:
        result=load(summary)
        return result.get('tasks',[])
    db=Path(base)/'production.sqlite'
    if not db.exists():return []
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    try:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
        if not exists:return []
        return [dict(json.loads(data),id=jid) for jid,data in conn.execute('SELECT id,data FROM jobs')]
    finally:conn.close()

def local_service(base):
    try:
        value=load(Path(base)/'99_工作区'/'视频预览服务.json').get('url','')
        parsed=urllib.parse.urlsplit(value)
        if parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port and not parsed.username:return value
    except (OSError,ValueError,TypeError):pass
    return None

def snapshot(project,base,out,tasks):
    groups={str(g['id']):g for g in project.get('groups',[])}
    actual={str(t['id']):t for t in tasks if str(t['id']) in groups or t.get('operation')=='video' or t.get('kind')=='video'}
    rows=[];counts={};files={}
    for gid in dict.fromkeys(list(groups)+list(actual)):
        group=groups.get(gid,{}); task=actual.get(gid,{})
        phase=task.get('phase','not_started'); output=None; warning=''
        if phase=='downloaded':
            try:
                output=resolve(base,task['output']).resolve()
                size=output.stat().st_size
                if not output.is_file() or size<=0:raise ValueError('empty')
                if task.get('bytes') is not None and int(task['bytes'])!=size:raise ValueError('size changed')
                if output.suffix.lower()!='.mp4':raise ValueError('not MP4')
                if not task.get('sha256'):raise ValueError('missing integrity record')
            except (OSError,ValueError,KeyError,TypeError):
                phase='file_unavailable';output=None;warning='文件缺失、长度变化或完整性记录未齐；核对原任务，不重新生成。'
        episode=group.get('episode',task.get('episode'))
        try:episode=int(episode)
        except (TypeError,ValueError):episode=None
        row={'id':gid,'episode':episode,'phase':phase,'attempt':task.get('attempts',0),
             'file_available':output is not None,'warning':warning,
             'user_review':'待用户审片','content_review_performed':False}
        if output:
            media_id=hashlib.sha256((str(output)+'|'+task['sha256']).encode()).hexdigest()
            try:local_url=urllib.parse.quote(Path(os.path.relpath(output,out)).as_posix(),safe='/')
            except ValueError:local_url=output.as_uri()
            row.update(src=local_url,href=local_url,bytes=size,sha256=task['sha256'],media_id=media_id,
                       version=media_id,filename=output.name)
            files[media_id]={'path':str(output),'bytes':size,'sha256':task['sha256']}
        counts[phase]=counts.get(phase,0)+1;rows.append(row)
    rows.sort(key=lambda r:(r['episode'] is None,r['episode'] or 0,r['id']))
    return {'schema_version':1,'project_id':project.get('project_id','项目'),
            'registered_groups':len(groups),'total':len(rows),'downloaded':counts.get('downloaded',0),
            'counts':counts,'items':rows,'files':files,'service_url':local_service(base),
            'generation_authorized':False,'content_review_performed':False}

def public_snapshot(data,served=False):
    result={k:v for k,v in data.items() if k!='files'}
    result['items']=[dict(r) for r in data['items']]
    if served:
        for row in result['items']:
            if row.get('file_available'):row['src']=row['href']='/media/'+row['media_id']
    return result

def callback(data,served=False):
    # Escapes also prevent filenames from terminating an inline fallback script.
    text=json.dumps(public_snapshot(data,served),ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return 'window.MuFlowVideoPreview.receive('+text+');\n'

def shell():
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>已出成片预览 · MuFlow</title>
<style>[hidden]{display:none!important}body{margin:0;background:#11151b;color:#e8edf3;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}header,main{max-width:1420px;margin:auto;padding:24px}header{padding-bottom:6px}h1{margin:0;font-size:26px}a{color:#9ccaff}button,select{background:#293546;color:white;border:1px solid #58708d;padding:9px;border-radius:7px;cursor:pointer}nav,.toolbar{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:18px}.card{padding:14px;background:#1b2430;border:1px solid #344357;border-radius:12px}.card h2{font-size:17px;margin:0 0 8px}video{display:block;width:100%;max-height:450px;background:#080b10;aspect-ratio:9/16}p{margin:8px 0}.muted{color:#b6c3d3}.error{color:#ffc2b0}.notice{color:#ffdc96}.card a{margin-right:14px}.actions{display:flex;gap:9px;flex-wrap:wrap;margin:10px 0}#empty{padding:30px;border:1px dashed #5a6e86}button:disabled{opacity:.6}.hidden{display:none}</style></head><body><header><h1>已出成片预览</h1><p id="summary">视频尚未开始；此入口已建立。</p><p id="stamp" class="muted">等待实际任务记录</p><nav><a href="../03_资产图/00_资产参考图总览.html">资产参考图总览</a><a href="../07_跑前预览/全剧跑前预览.html">全剧跑前预览</a><a id="browser-link" hidden target="_blank" rel="noopener">本地浏览器播放</a></nav><p class="muted">按集、组排序；点击播放才加载对应视频。队列更新只改进度和新增卡片，保留已有播放器、播放位置及暂停状态。文件就绪不代表内容验收通过。</p><div class="toolbar"><button id="refresh" type="button">更新进度</button><label><input type="checkbox" id="auto" checked>自动更新进度</label><label><input type="checkbox" id="show-pending">显示尚未出片的任务</label></div><p id="update-error" class="notice"></p></header><main><div id="empty">暂无可播放文件。未开始、生成中、待下载与异常任务将在实际登记后显示。</div><div id="videos" class="grid"></div></main><script src="video-preview.js"></script><script src="成片进度.js"></script></body></html>'''

def build(project_path,tasks=None,summary=None):
    pp=Path(project_path);base=pp.parent;project=load(pp)
    return build_data(project,base,tasks if tasks is not None else read_tasks(base,summary))

def build_data(project,base,tasks):
    base=Path(base);out=base/'08_视频';out.mkdir(parents=True,exist_ok=True)
    data=snapshot(project,base,out,tasks);manifest=out/MANIFEST
    old=load(manifest) if manifest.exists() else None
    body_digest=digest(data)
    data['updated_at']=old.get('updated_at') if old and old.get('data_digest')==body_digest else datetime.datetime.now(datetime.timezone.utc).isoformat()
    data['data_digest']=body_digest
    changed=write_changed(manifest,json.dumps(data,ensure_ascii=False,indent=2))
    write_changed(out/CLIENT,(Path(__file__).resolve().parents[1]/'assets'/'video-preview.js').read_bytes())
    panel_changed=write_changed(out/PANEL,shell())
    write_changed(out/DATA,callback(data))
    return {'panel':str(out/PANEL),'data':str(manifest),'downloaded':data['downloaded'],'total':data['total'],
            'data_changed':changed,'panel_changed':panel_changed,'service_url':data['service_url'],'media_calls':0}

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='更新已出成片预览，不生成、不上传、不检查视频内容')
    ap.add_argument('project');ap.add_argument('--summary');a=ap.parse_args()
    print(json.dumps(build(a.project,summary=a.summary),ensure_ascii=False))
