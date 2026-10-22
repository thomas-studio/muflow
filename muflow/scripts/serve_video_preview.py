"""Loopback-only video viewer with seek support. No generation, decoding or arbitrary file serving."""
from __future__ import annotations
import argparse, contextlib, hashlib, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from core import load,save,digest
from video_gallery import PANEL,DATA,CLIENT,MANIFEST,build,build_data,read_tasks,callback

def byte_range(value,size):
    if not value:return 0,size-1,False
    m=re.fullmatch(r'bytes=(\d*)-(\d*)',value.strip())
    if not m or not any(m.groups()):raise ValueError('Invalid range')
    a,b=m.groups()
    if not a:
        length=int(b)
        if length<=0:raise ValueError('Invalid suffix')
        start=max(0,size-length);end=size-1
    else:
        start=int(a);end=min(int(b),size-1) if b else size-1
    if start>=size or start>end:raise ValueError('Unsatisfiable range')
    return start,end,True

def handler(directory,project_id,update=None):
    folder=Path(directory)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_HEAD(self):self.respond(head=True)
        def do_GET(self):self.respond()
        def respond(self,head=False):
            # Do not expose a project tree, credentials, receipts or signed URLs.
            host=self.headers.get('Host','').split(':')[0]
            if host not in ('127.0.0.1','localhost'):self.send_error(403);return
            route=urllib.parse.urlsplit(self.path).path
            try:
                if route=='/health':
                    data=json.dumps({'service':'muflow-video-preview','project_id':project_id}).encode()
                    return self.bytes(data,'application/json',head)
                if route in ('/','/index.html'):
                    page=(folder/PANEL).read_text(encoding='utf-8')
                    page=re.sub(r'<a href="../(?:03_资产图|07_跑前预览)/[^"]+">.*?</a>','',page)
                    return self.bytes(page.encode(),'text/html; charset=utf-8',head)
                if route=='/'+CLIENT:return self.bytes((folder/CLIENT).read_bytes(),'text/javascript; charset=utf-8',head)
                if route=='/'+urllib.parse.quote(DATA) or urllib.parse.unquote(route)=='/'+DATA:
                    if update:update()
                    return self.bytes(callback(load(folder/MANIFEST),served=True).encode(),'text/javascript; charset=utf-8',head)
                if route.startswith('/media/'):
                    mid=route[len('/media/'):]
                    if not re.fullmatch('[a-f0-9]{64}',mid):self.send_error(404);return
                    item=load(folder/MANIFEST).get('files',{}).get(mid)
                    if not item:self.send_error(404);return
                    file=Path(item['path'])
                    if not file.is_file() or file.stat().st_size!=item['bytes']:self.send_error(409,'Local video requires recovery');return
                    return self.media(file,item,head)
                self.send_error(404)
            except (OSError,ValueError,KeyError,TypeError):self.send_error(503,'Preview data unavailable; retry progress update')
        def bytes(self,data,mime,head):
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
            if not head:self.wfile.write(data)
        def media(self,file,item,head):
            size=item['bytes'];etag='"'+item['sha256']+'"'
            requested=self.headers.get('Range')
            if self.headers.get('If-Range') not in (None,etag):requested=None
            try:start,end,partial=byte_range(requested,size)
            except ValueError:
                self.send_response(416);self.send_header('Content-Range','bytes */'+str(size));self.send_header('Content-Length','0');self.end_headers();return
            self.send_response(206 if partial else 200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('ETag',etag);self.send_header('Cache-Control','private, max-age=3600');self.send_header('X-Content-Type-Options','nosniff')
            if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if head:return
            try:
                with file.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(1024*256,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
            except (BrokenPipeError,ConnectionResetError):pass
    return Handler

def sync_view(project_path,view_root=None):
    pp=Path(project_path)
    if view_root is None:return build(pp)
    tasks=read_tasks(pp.parent)
    for task in tasks:
        if task.get('output'):task['output']=str((pp.parent/task['output']).resolve())
    return build_data(load(pp),Path(view_root),tasks)

def create_server(project_path,port=0,view_root=None):
    pp=Path(project_path);p=load(pp);sync_view(pp,view_root)
    folder=(Path(view_root) if view_root is not None else pp.parent)/'08_视频'
    return ThreadingHTTPServer(('127.0.0.1',port),handler(folder,p.get('project_id',pp.parent.name),lambda:sync_view(pp,view_root)))

def background(project_path,view_root=None):
    pp=Path(project_path).resolve();p=load(pp);view_base=Path(view_root).resolve() if view_root else pp.parent;state=view_base/'99_工作区'/'视频预览服务.json'
    if state.exists():
        try:
            prior=load(state);url=urllib.parse.urlsplit(prior['url'])
            if url.scheme=='http' and url.hostname=='127.0.0.1' and url.port:
                with urllib.request.urlopen(prior['url']+'health',timeout=1) as response:health=json.load(response)
                if health.get('service')=='muflow-video-preview' and health.get('project_id')==p.get('project_id',pp.parent.name):
                    sync_view(pp,view_root);return prior
        except (OSError,ValueError,KeyError):pass
    command=[sys.executable,str(Path(__file__).resolve()),str(pp)]
    if view_root:command+=['--out',str(Path(view_root).resolve())]
    kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'cwd':str(pp.parent)}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS
    else:kwargs['start_new_session']=True
    process=subprocess.Popen(command,**kwargs)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if state.exists():
            record=load(state)
            if record.get('pid')==process.pid:return record
        if process.poll() is not None:raise RuntimeError('Local preview service failed to start')
        time.sleep(.1)
    raise RuntimeError('Local preview service startup pending; check local process, do not generate again')

def serve(project_path,port=0,view_root=None):
    pp=Path(project_path).resolve();server=create_server(pp,port,view_root);view_base=Path(view_root).resolve() if view_root else pp.parent;state=view_base/'99_工作区'/'视频预览服务.json'
    record={'url':f'http://127.0.0.1:{server.server_port}/','pid':os.getpid(),'project_id':load(pp).get('project_id',pp.parent.name),'media_calls':0}
    save(state,record);sync_view(pp,view_root)
    try:server.serve_forever(poll_interval=.3)
    finally:
        server.server_close()
        if state.exists() and load(state).get('pid')==os.getpid():
            state.unlink();sync_view(pp,view_root)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='本机浏览器播放入口，仅服务已登记视频；不生成、不上传、不审片')
    ap.add_argument('project');ap.add_argument('--out',help='Optional isolated viewer directory; leaves production project untouched');ap.add_argument('--port',type=int,default=0);ap.add_argument('--background',action='store_true');a=ap.parse_args()
    if a.background:print(json.dumps(background(a.project,a.out),ensure_ascii=False))
    else:serve(a.project,a.port,a.out)
