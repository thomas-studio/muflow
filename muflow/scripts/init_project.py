"""Create three durable local preview entrances before parsing the source.

A failed/unsupported source or one failed renderer leaves independently
usable placeholder pages. No API or media generation is performed.
"""
import argparse,hashlib,html,json,os,tempfile,uuid
from pathlib import Path
from core import load,save
from extract_source import extract

STATE='muflow_init_v240'
PANELS={
 'assets':('03_资产图','00_资产参考图总览.html','资产参考图总览'),
 'preview':('07_跑前预览','全剧跑前预览.html','全剧跑前预览'),
 'videos':('08_视频','已出成片预览.html','已出成片预览'),
}
MARK='<!-- MUFLOW_INIT_V240_PLACEHOLDER -->'

def _hash(data):return hashlib.sha256(data).hexdigest()

def _rel(folder,name):return folder+'/'+name

def _placeholder(title,status):
    links=' '.join('<a href="../'+html.escape(folder+'/'+name,quote=True)+'">'+html.escape(label)+'</a>' for folder,name,label in PANELS.values())
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
       '<meta name="viewport" content="width=device-width,initial-scale=1">'
       '<title>'+html.escape(title)+'</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;'
       'max-width:900px;margin:40px auto;padding:0 20px;color:#263345}nav a{margin-right:18px}'
       '.box{padding:20px;border:1px solid #ccd8e1;border-radius:10px;background:#f7fafc}</style></head><body>'
       +MARK+'<h1>'+html.escape(title)+'</h1><nav>'+links+'</nav><div class="box"><h2>准备中</h2><p>'
       +html.escape(status)+'</p><p>集数和内容尚未确认；图片、视频状态以实际任务记录为准。重新打开或刷新页面可查看更新。</p>'
       '</div></body></html>').encode('utf-8')

def _persist(project_path,project):save(project_path,project)

def _save_state(state):
    save(state['state_file'],state)

def _reconcile_pending(base,state,issues):
    for rel,entry in list(state.get('pending_files',{}).items()):
        target=(base/rel).resolve()
        if not target.is_relative_to(base):
            issues.append({'object':'initialization','result':'待复核','error':'状态中的路径越过项目目录'});continue
        current=_hash(target.read_bytes()) if target.is_file() else None
        if current==entry['after']:
            state['owned_files'][rel]=current
        elif current==entry['before']:
            pass  # interrupted before replacement: retry against last known file
        else:
            issues.append({'object':'initialization','result':'待复核','error':'中断后文件已变化，已保留：'+rel});continue
        del state['pending_files'][rel]
    _save_state(state)

def _replace_owned(path,content,state,relative):
    """Replace only absent files or files still byte-identical to our last write."""
    path=Path(path);project_root=Path(state['project_root']).resolve()
    if not path.resolve().is_relative_to(project_root):raise ValueError('目标文件越过项目目录，已拒绝写入')
    path.parent.mkdir(parents=True,exist_ok=True);owned=state['owned_files']
    exists=path.exists()
    if exists and (not path.is_file() or owned.get(relative)!=_hash(path.read_bytes())):
        raise ValueError('发现已有或已修改文件，已保留：'+relative)
    state.setdefault('pending_files',{})[relative]={'before':owned.get(relative) if exists else None,'after':_hash(content)}
    _save_state(state)
    if exists:
        tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
        try:tmp.write_bytes(content);os.replace(tmp,path)
        finally:
            if tmp.exists():tmp.unlink()
    else:
        with path.open('xb') as stream:stream.write(content)
    owned[relative]=_hash(content);state['pending_files'].pop(relative,None);_save_state(state)

def _update_placeholders(base,state,message,issues):
    from batch_video import blocking_exclusive
    with blocking_exclusive(base/'.preview-refresh.lock'):
        _update_placeholders_unlocked(base,state,message,issues)

def _update_placeholders_unlocked(base,state,message,issues):
    for key,(folder,name,title) in PANELS.items():
        rel=_rel(folder,name);p=base/folder/name
        try:
            if p.is_file() and MARK.encode() in p.read_bytes():
                _replace_owned(p,_placeholder(title,message),state,rel)
        except (OSError,ValueError) as exc:
            issues.append({'object':key,'result':'待复核','error':str(exc)})

def _create_placeholders(base,state,issues):
    from batch_video import blocking_exclusive
    with blocking_exclusive(base/'.preview-refresh.lock'):
        _create_placeholders_unlocked(base,state,issues)

def _create_placeholders_unlocked(base,state,issues):
    for key,(folder,name,title) in PANELS.items():
        rel=_rel(folder,name);path=base/folder/name
        try:
            if path.exists():
                if state['owned_files'].get(rel)!=_hash(path.read_bytes()):
                    raise ValueError('入口已有文件，保留不覆盖：'+rel)
            else:
                _replace_owned(path,_placeholder(title,'已建立入口，等待原稿提取。'),state,rel)
        except (OSError,ValueError) as exc:
            issues.append({'object':key,'result':'待复核','error':str(exc)})

def _source(base,source,state):
    path=base/'source.json';source=Path(source)
    if path.exists():
        cached=load(path)
        if cached.get('_muflow_init_owner')!=state['id']:
            raise ValueError('source.json 已存在且非本次初始化创建，已保留。')
        if not source.is_file() or cached.get('sha256')!=_hash(source.read_bytes()):
            raise ValueError('原稿已变化或缺失，保留已有提取结果，需另建版本核对。')
        return cached
    data=extract(source);data['_muflow_init_owner']=state['id']
    raw=(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    with path.open('xb') as stream:stream.write(raw)
    return data

def _renderer(key,project):
    if key=='assets':
        from asset_gallery import build
        return build(project)
    if key=='preview':
        from build_preview import build
        return build(project,Path(project).parent/'07_跑前预览')
    from video_gallery import build
    return build(project)

def _relocated_bytes(file,stage,base):
    """Rewrite the temporary builder root in textual paths and file-URI links."""
    raw=file.read_bytes()
    if file.suffix.lower() not in ('.html','.htm','.json','.js','.md','.txt'):
        return raw
    content=raw.decode('utf-8')
    # HTML file links are URI-encoded; JSON text paths escape backslashes.
    old,new=str(stage),str(base)
    content=content.replace(stage.as_uri(),base.as_uri())
    content=content.replace(json.dumps(old,ensure_ascii=False)[1:-1],json.dumps(new,ensure_ascii=False)[1:-1])
    content=content.replace(old,new)
    return content.encode('utf-8')

def _render_one(base,project,key,state):
    """Build in an isolated same-project folder, then publish nonconflicting files."""
    base=base.resolve();folder,entry,_=PANELS[key]
    with tempfile.TemporaryDirectory(prefix='.muflow-init-',dir=str(base)) as tmp:
        stage=Path(tmp).resolve()
        # TemporaryDirectory recursively removes only this freshly created child.
        if stage.parent!=base or not stage.name.startswith('.muflow-init-'):
            raise ValueError('临时目录不在目标项目内')
        save(stage/'project.json',project)
        (stage/'source.json').write_bytes((base/'source.json').read_bytes())
        result=_renderer(key,stage/'project.json')
        source_folder=stage/folder
        if not (source_folder/entry).is_file():raise ValueError('构建器未生成预览入口')
        items=[p for p in source_folder.rglob('*') if p.is_file()]
        if not items:raise ValueError('构建器未生成任何文件')
        # Share the target project's local preview lock with later refreshes.
        from batch_video import blocking_exclusive
        with blocking_exclusive(base/'.preview-refresh.lock'):
            # Check all targets before copying to avoid partial replacement on conflict.
            for file in items:
                rel=(Path(folder)/file.relative_to(source_folder)).as_posix();dest=base/rel
                if dest.exists() and (not dest.is_file() or state['owned_files'].get(rel)!=_hash(dest.read_bytes())):
                    raise ValueError('已有或已修改文件，保留不覆盖：'+rel)
            for file in items:
                rel=(Path(folder)/file.relative_to(source_folder)).as_posix()
                _replace_owned(base/rel,_relocated_bytes(file,stage,base),state,rel)
        # The original renderer result may contain temporary paths: expose the real entry.
        result=dict(result or {});result['panel']=str(base/folder/entry)
        for k,v in list(result.items()):
            if isinstance(v,str) and v.startswith(str(stage)):
                result[k]=str(base/Path(v).relative_to(stage))
        return result

def initialize(directory,source=None,project_id=None):
    base=Path(directory).resolve();base.mkdir(parents=True,exist_ok=True);pp=base/'project.json'
    # A directory-only request establishes the previews before discovering inputs.
    source_path=str(Path(source).resolve()) if source and Path(source).resolve()!=base else None
    issues=[]
    if pp.exists():
        project=load(pp);meta=project.get(STATE)
        if not isinstance(meta,dict) or not meta.get('id') or not meta.get('state_path'):
            raise ValueError('Project exists; use refresh_progress.py to resume without overwriting')
        state_file=(base/meta['state_path']).resolve()
        if not state_file.is_relative_to(base) or not state_file.is_file():
            raise ValueError('初始化状态文件缺失或越过项目目录，原项目已保留。')
        state=load(state_file);state['state_file']=str(state_file);state['project_root']=str(base)
        if state.get('id')!=meta['id'] or state.get('status')=='complete':
            raise ValueError('Project exists; use refresh_progress.py to resume without overwriting')
        _reconcile_pending(base,state,issues)
        prior=project.get('input_source_path')
        if source_path and prior and prior!=source_path:
            if state.get('status') not in ('source_failed','source_choice_pending','source_not_found','source_pending') or (base/'source.json').exists():
                raise ValueError('原稿路径与原初始化任务不同；原项目已保留。')
        if not source_path:source_path=prior
    else:
        project=load(Path(__file__).resolve().parents[1]/'assets'/'project.template.json')
        project['project_id']=project_id or base.name;project['source_path']='source.json';project['input_source_path']=source_path
        init_id=uuid.uuid4().hex;state_rel='99_工作区/初始化进度-'+init_id+'.json';state_path=base/state_rel
        state={'id':init_id,'state_file':str(state_path),'project_root':str(base),'status':'creating_previews','owned_files':{},'pending_files':{}}
        project[STATE]={'id':init_id,'state_path':state_rel}
        # Exclusive creation preserves an existing project if another task starts concurrently.
        with pp.open('x',encoding='utf-8') as stream:json.dump(project,stream,ensure_ascii=False,indent=2)
        state_path.parent.mkdir(parents=True,exist_ok=True)
        with state_path.open('x',encoding='utf-8') as stream:json.dump(state,stream,ensure_ascii=False,indent=2)
    _create_placeholders(base,state,issues);state['status']='source_pending';_save_state(state)
    entrances={key:str(base/folder/name) for key,(folder,name,_) in PANELS.items()}
    print(json.dumps({'event':'initial_previews_created','project':str(pp),'entrances':entrances,'issues':issues},ensure_ascii=False),flush=True)
    out={'media_calls':0,'issues':issues,'project':str(pp),'entrances':entrances}
    if not source_path:
        candidates=sorted((p.resolve() for p in base.iterdir() if p.is_file() and p.suffix.lower() in ('.txt','.md','.docx','.pdf')),key=lambda p:p.name.casefold())
        if len(candidates)!=1:
            state['status']='source_choice_pending' if candidates else 'source_not_found'
            state['source_candidates']=[str(p) for p in candidates]
            message=('发现多个可能的原稿，请指定其中一个：'+ '、'.join(p.name for p in candidates)) if candidates else '未找到原稿，请提供文件路径。'
            issues.append({'object':'source','result':'待复核','error':message})
            _update_placeholders(base,state,message,issues);_save_state(state)
            out['status']=state['status'];out['source_candidates']=state['source_candidates'];return out
        source_path=str(candidates[0])
    project['input_source_path']=source_path;state.pop('source_candidates',None);_persist(pp,project)
    try:
        source_data=_source(base,source_path,state)
    except Exception as exc:
        state['status']='source_failed';state['source_error']=str(exc)
        issues.append({'object':'source','result':'待复核','error':str(exc)})
        _update_placeholders(base,state,'原稿提取失败：'+str(exc),issues);_save_state(state)
        out['status']='source_failed';return out
    state.pop('source_error',None);state['status']='rendering_previews';state['source_sha256']=source_data['sha256'];_save_state(state)
    for key in PANELS:
        try:out[key]=_render_one(base,project,key,state)
        except Exception as exc:
            issues.append({'object':key,'result':'待复核','error':str(exc)})
            folder,name,title=PANELS[key];path=base/folder/name
            try:
                from batch_video import blocking_exclusive
                with blocking_exclusive(base/'.preview-refresh.lock'):
                    if path.is_file() and MARK.encode() in path.read_bytes():
                        _replace_owned(path,_placeholder(title,'该入口更新失败：'+str(exc)),state,_rel(folder,name))
            except (OSError,ValueError) as inner:
                issues.append({'object':key,'result':'待复核','error':str(inner)})
        _save_state(state)
    state['status']='render_partial' if issues else 'complete';_save_state(state)
    out['status']=state['status'];return out

def _owned_state(project,base):
    meta=project.get(STATE)
    if not isinstance(meta,dict) or not meta.get('id') or not meta.get('state_path'):
        raise ValueError('旧项目未登记初始化归属，不自动覆盖既有预览；请先人工核对并迁移。')
    path=(base/meta['state_path']).resolve()
    if not path.is_relative_to(base) or not path.is_file():raise ValueError('初始化归属状态缺失或越过项目目录')
    state=load(path);state['state_file']=str(path);state['project_root']=str(base)
    if state.get('id')!=meta['id'] or state.get('status')!='complete':raise ValueError('初始化未完成或有文件冲突，不能执行自动刷新')
    if state.get('refresh_in_progress'):raise ValueError('上次预览刷新意外中断；保留文件，待人工核对归属')
    return state

def _refresh_targets(key,project,base):
    """Enumerate all current deterministic output targets before any writes."""
    fixed={
      'assets':['03_资产图/00_资产参考图总览.html','03_资产图/当前候选图片清单.json','03_资产图/全部迭代追溯.json','03_资产图/实际资产提示词.md','03_资产图/00_生图说明与索引.md','03_资产图/资产图册快照.json'],
      'preview':['07_跑前预览/全剧跑前预览.html','07_跑前预览/run_scope.json','07_跑前预览/compiled_jobs.json','07_跑前预览/quote_snapshot.json','07_跑前预览/timing_plan.json','07_跑前预览/preview_checks.json'],
      'videos':['08_视频/已出成片预览.html','08_视频/成片进度.js','08_视频/成片清单.json','08_视频/video-preview.js'],
    }
    targets={name:None for name in fixed[key]}  # None = may be overwritten: must be owned
    if key=='assets':
        from asset_gallery import manifest,_plan
        images,_,_=_plan(manifest(project,base),base,base/'03_资产图')
        for item in images:
            dest=(base/'03_资产图'/item['file']).resolve()
            if not dest.is_relative_to(base):raise ValueError('图册目标越过项目目录')
            targets[dest.relative_to(base).as_posix()]=item['sha256'] # copy_exact never overwrites
    if key=='preview':
        import io
        from PIL import Image
        from core import resolve
        by={a['id']:a for a in project.get('assets',[])}
        ids={b.get('asset_id') for g in project.get('groups',[]) for b in g.get('bindings',[])}
        for aid in ids:
            a=by.get(aid,{})
            if not a.get('local_path'):continue
            src=resolve(base,a['local_path'])
            try:
                data=src.read_bytes();actual=_hash(data)
                with Image.open(src) as opened:
                    opened.load();im=opened.convert('RGB')
                im.thumbnail((420,300));buf=io.BytesIO();im.save(buf,'JPEG',quality=82)
            except (OSError,ValueError,TypeError):continue
            prefix='07_跑前预览/preview_resources/'+actual
            targets[prefix+(src.suffix.lower() or '.img')]=actual
            targets[prefix+'-thumb.jpg']=_hash(buf.getvalue())
    return targets

def _check_refresh_targets(base,state,targets):
    before={}
    for rel,create_only_hash in targets.items():
        path=(base/rel).resolve()
        if not path.is_relative_to(base):raise ValueError('刷新目标越过项目目录：'+rel)
        if not path.exists():before[rel]=None;continue
        if not path.is_file():raise ValueError('刷新目标不是文件，已保留：'+rel)
        current=_hash(path.read_bytes());owned=state['owned_files'].get(rel)
        if owned==current:before[rel]=current;continue
        # The builders only create these content-addressed copies when absent.
        if create_only_hash and current==create_only_hash:before[rel]='unowned-identical';continue
        raise ValueError('已有或已修改文件，保留不覆盖：'+rel)
    return before

def refresh_owned(project_path,profile_path=None):
    """Refresh existing real assets only after checking every known write target.

    Old projects or interrupted writes need explicit manual reconciliation. This
    function never submits media, silently adopts files, or refreshes via staging.
    """
    from batch_video import blocking_exclusive
    from core import digest
    pp=Path(project_path).resolve();base=pp.parent;out={'media_calls':0,'issues':[]}
    with blocking_exclusive(base/'.asset-registry.lock'):
      with blocking_exclusive(base/'.preview-refresh.lock'):
        project=load(pp)
        try:
            state=_owned_state(project,base);_reconcile_pending(base,state,out['issues'])
            if out['issues'] or state.get('pending_files'):raise ValueError('初始化写入尚未核对完毕，保留原文件')
        except (OSError,ValueError,KeyError) as exc:
            out['issues'].append({'object':'ownership','result':'待复核','error':str(exc)});return out
        project_digest=digest(project)
        for key in PANELS:
            before={}
            try:
                targets=_refresh_targets(key,project,base)
                before=_check_refresh_targets(base,state,targets)
                # A crash after writes cannot silently adopt unknown files.
                state['refresh_in_progress']={'panel':key,'project_digest':project_digest};_save_state(state)
                if key=='assets':
                    from asset_gallery import _build_unlocked
                    result=_build_unlocked(pp)
                elif key=='preview':
                    from build_preview import build
                    result=build(pp,base/'07_跑前预览',profile_path)
                else:
                    from video_gallery import build
                    result=build(pp)
                out[key]=result
                if digest(load(pp))!=project_digest:
                    out['issues'].append({'object':key,'result':'待复核','error':'项目在刷新期间发生变化，需重新刷新校验'})
            except Exception as exc:
                out['issues'].append({'object':key,'result':'待复核','error':str(exc)})
            finally:
                if state.get('refresh_in_progress',{}).get('panel')==key:
                    # Targets were prechecked and held under shared locks; do not
                    # adopt pre-existing unowned identical input image copies.
                    for rel,old in before.items():
                        path=(base/rel).resolve()
                        if old!='unowned-identical' and path.is_file():state['owned_files'][rel]=_hash(path.read_bytes())
                    state.pop('refresh_in_progress',None);_save_state(state)
        # Audit and progress sidecars are ordinary project outputs: they must
        # pass the same ownership check as the three preview pages.
        report_rel='03_资产图/资产审核问题汇总.md'
        try:
            _check_refresh_targets(base,state,{report_rel:None})
            from selfcheck_report import build as build_review_report
            _replace_owned(base/report_rel,build_review_report([pp]).encode('utf-8'),state,report_rel)
            out['review_report']=str(base/report_rel)
        except Exception as exc:
            out['issues'].append({'object':'review_report','result':'待复核','error':str(exc)})
        out['asset_table']=project.get('deliverables',{}).get('asset_table')
        status_rel='99_工作区/面板刷新状态.json'
        try:
            _check_refresh_targets(base,state,{status_rel:None})
            out['status_file']=str(base/status_rel)
            content=(json.dumps(out,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
            _replace_owned(base/status_rel,content,state,status_rel)
        except Exception as exc:
            out.pop('status_file',None)
            out['issues'].append({'object':'refresh_status','result':'待复核','error':str(exc)})
        return out

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('directory');ap.add_argument('source',nargs='?');ap.add_argument('--project-id');a=ap.parse_args()
    result=initialize(a.directory,a.source,a.project_id)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['status']=='complete' else 1)
