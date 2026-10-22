"""Refresh one local/OSS run panel and freeze exact request sidecars.

No upload, API probe or media submission occurs. A useful local panel can be
delivered while text, references or remote URLs are pending.
"""
import argparse
import base64
import datetime
import io
import json
from pathlib import Path
from core import load, save, prompt, resolve, digest
from validate_project import validate
from workflow_gate import content_digest
from timing_plan import build_plan, render_summary
from source_coverage import coverage
from preview_ui import (PANEL, archive_current, details, document, esc,
                        image_card, image_resource, scene_text, sound_cell,
                        state_text, value_text)


def thumbnail(path):
    """Legacy helper; current pages use shared local resource files."""
    from PIL import Image
    with Image.open(path) as opened:
        im = opened.convert('RGB')
    im.thumbnail((420, 300))
    stream = io.BytesIO()
    im.save(stream, 'JPEG', quality=82)
    return 'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()


def compile_jobs(data, base):
    # Missing references remain unprepared; no placeholder becomes executable.
    assets = {a['id']:a for a in data.get('assets', [])}
    items, unprepared = [], []
    for group in data.get('groups', []):
        try:
            refs = []
            for binding in group.get('bindings', []):
                asset = assets[binding['asset_id']]
                refs.append({'name':binding['name'], 'asset_id':asset['id'],
                             'path':str(resolve(base, asset['local_path'])),
                             'sha256':asset.get('sha256'), 'url':asset.get('remote_url')})
            text = prompt(base, group)
            items.append({'id':group['id'], 'episode':group['episode'], 'prompt':text,
                          'prompt_sha256':digest(text), 'duration':group['expected_seconds'],
                          'ratio':data['settings']['video_ratio'],
                          'schema_version':data.get('schema_version', 1), 'project_id':data['project_id'],
                          'project_version':data['version'],
                          'speech_segments':[f for s in group.get('shots', []) for f in s.get('speech', [])],
                          'images':refs, 'uses':group.get('uses', [])})
        except (KeyError, OSError, ValueError, TypeError) as exc:
            unprepared.append({'id':group.get('id'), 'episode':group.get('episode'), 'reason':str(exc)})
    return {'project_id':data.get('project_id'), 'version':data.get('version'),
            'schema_version':data.get('schema_version', 1), 'model':data.get('settings', {}).get('video_model'),
            'source_digest':content_digest(data), 'items':items, 'unprepared':unprepared}


def _validation(data, base, stage):
    try:
        return validate(data, base, data.get('groups', []), stage=stage)
    except Exception as exc:
        return {'issues':[{'id':'PREVIEW_VALIDATION', 'object':'project', 'actual':str(exc), 'stage':stage}]}


def _episode_ids(group):
    result = set()
    for use in group.get('uses') or [{'episode':group.get('episode')}]:
        try:
            result.add(int(use['episode']))
        except (ValueError, KeyError, TypeError):
            pass
    return result


def _requested_ids(spec, fallback):
    if not spec:
        return list(fallback)
    ids = set()
    for part in str(spec).split(','):
        if '-' in part:
            start, end = map(int, part.split('-', 1))
            if start < 1 or end < start:
                raise ValueError('Invalid episode range: '+part)
            ids.update(range(start, end+1))
        else:
            ids.add(int(part))
    if any(i < 1 for i in ids):
        raise ValueError('Episode numbers must be positive')
    return sorted(ids)


def _range_text(ids):
    return '、'.join(str(n) for n in ids) if ids else '待核实'


def _progress(data, base):
    try:
        from stage_queue import progress
        return progress(data, base)
    except ImportError:
        return {'state':'未初始化', 'messages':['阶段队列尚未初始化']}
    except Exception as exc:
        return {'state':'待核对', 'messages':[str(exc)]}


def _seconds(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _selected_ids(group,episode):
    uses = [u for u in group.get('uses',[]) if _episode_ids({'uses':[u]}) == {episode}]
    return set().union(*(set(u.get('shot_ids',[])) for u in uses)) if uses else {s.get('id') for s in group.get('shots',[])}


def _readiness(data,base):
    try:
        from review_state import readiness
        return readiness(data,base)
    except (ImportError,AttributeError):
        return {'label':'准备中','evidence':['完整文本、图片审阅和问题复核尚待核实']}
    except Exception as exc:
        return {'label':'准备中','evidence':['状态核对未完成：'+str(exc)]}


def _text_materials(data,base):
    files = []
    for kind,value in data.get('deliverables',{}).items():
        try:
            file = resolve(base,value).resolve()
            label = '<a href="'+esc(file.as_uri())+'" target="_blank" rel="noopener">'+esc(kind)+' · '+esc(file.name)+'</a>' if file.is_file() else esc(kind)+' · '+esc(value)+'（文件待准备）'
        except (TypeError,ValueError,OSError):
            label = esc(kind)+'（路径待准备）'
        files.append('<li>'+label+'</li>')
    content = '<p>剧本、分镜、资产表及两类提示词在本地逐步形成；文件链接和待补状态按当前登记显示。</p><ul>'+(''.join(files) or '<li>交付文件尚未登记</li>')+'</ul>'
    for asset in data.get('assets',[]):
        content += details(str(asset.get('id',''))+' · '+str(asset.get('name',''))+' · 资产提示词', '<p>方式：'+esc(asset.get('mode','待登记'))+'；母图：'+esc(asset.get('parent_id') or '无')+'</p><pre>'+esc(asset.get('prompt') or '资产提示词待准备')+'</pre>')
    return '<section id="text-deliverables">'+details('文字交付文件与全部资产提示词',content)+'</section>'


def _entry_links(data,base):
    from asset_gallery import GALLERY
    gallery = (Path(base)/'03_资产图'/GALLERY).resolve()
    links = '<a href="'+esc(gallery.as_uri())+'">资产总览</a> · '
    from video_gallery import PANEL as VIDEO_PANEL
    videos=(Path(base)/'08_视频'/VIDEO_PANEL).resolve()
    links += '<a href="'+esc(videos.as_uri())+'">已出成片预览</a> · '
    table = data.get('deliverables',{}).get('asset_table')
    if table:
        try:
            file = resolve(base,table).resolve()
            links += '<a href="'+esc(file.as_uri())+'">资产拆解表</a>' if file.is_file() else '资产拆解表：文件待编制'
        except (OSError,ValueError,TypeError):
            links += '资产拆解表：路径待登记'
    else:
        links += '资产拆解表：待编制'
    return '<p class="entry-links">'+links+' · <a href="#overview">当前跑前预览</a></p>'


def _group_card(data, group, episode, assets, resources, previous, following, item):
    gid = group.get('id', '未编号组')
    shots = group.get('shots', [])
    scenes = {s.get('id'):s for s in data.get('scenes', [])}
    selected = _selected_ids(group,episode)
    selected_seconds = sum(_seconds(s.get('seconds')) for s in shots if s.get('id') in selected)
    bindings = group.get('bindings', [])
    rows = ['<article class="card" id="ep-'+str(episode)+'-group-'+esc(gid)+'" data-group-id="'+esc(gid)+'"><header class="chd"><h3>'+esc(gid)+' · '+esc(group.get('title',group.get('name','30秒素材组')))+'</h3><div class="sub">本集使用 '+esc(f'{selected_seconds:g}')+' 秒 / 完整 '+esc(group.get('expected_seconds','待定'))+' 秒 · '+str(len(selected))+' / '+str(len(shots))+' 镜 · '+str(len(bindings))+' 张参考图；相同组号仅生成一次</div></header><div class="cbd">']
    rows.append('<div class="bind">'+(' / '.join('@'+esc(b.get('name',''))+'=图'+str(i) for i,b in enumerate(bindings,1)) or '参考图绑定待准备')+'</div>')
    rows.append('<div class="refs">'+''.join(image_card(i,b,assets.get(b.get('asset_id'),{}),resources.get(b.get('asset_id'),{'present':False,'reason':'资产未登记'})) for i,b in enumerate(bindings,1))+'</div>')
    rows.append('<p class="bridge">← 承上：'+esc(group.get('entry_state') or (state_text(previous,True) if previous else '本剧起点；按本组首镜进入状态'))+'</p>')
    used_scenes = list(dict.fromkeys(s.get('scene_id') for s in shots if s.get('id') in selected))
    scene_summary = ' → '.join(scene_text(scenes.get(sid,{'id':sid})) for sid in used_scenes)
    rows.append('<dl class="kv"><dt>场景／日夜</dt><dd>'+esc(scene_summary or '场景与时刻待补')+'</dd><dt>声音规则</dt><dd>仅表内角色发声和必要物理音效；声道、情绪按各句登记。无背景音乐、无环境氛围音。</dd><dt>动作／锁定</dt><dd>'+esc(group.get('directing_notes') or group.get('continuity_notes') or '以逐镜动作与承接状态为准')+'</dd></dl>')
    rows.append('<div class="table-wrap"><table class="shots"><thead><tr><th>镜号</th><th>时长</th><th>景别／机位／运动</th><th>画面与动作</th><th>对白／音效</th></tr></thead><tbody>')
    for shot in shots:
        active = shot.get('id') in selected
        scene = scenes.get(shot.get('scene_id'), {'id':shot.get('scene_id')})
        rows.append('<tr'+(' class="unused" title="本集不使用；保留完整请求上下文"' if not active else '')+' data-shot-id="'+esc(shot.get('id'))+'"><td>'+esc(shot.get('id'))+'</td><td>'+esc(shot.get('seconds','待定'))+'秒</td><td>'+esc(shot.get('camera') or '机位待补')+'</td><td><span class="scene">'+esc(scene_text(scene))+'</span>'+esc(shot.get('action') or '画面与动作待补')+'</td><td>'+sound_cell(shot)+'</td></tr>')
    rows.append('</tbody></table></div>')
    used_shots = [s for s in shots if s.get('id') in selected]
    rows.append('<p class="bridge endframe">末帧：'+esc(state_text(used_shots[-1],True) if used_shots else '尚无镜头')+'</p>')
    rows.append('<p class="bridge">→ 下一组／下一集：'+esc(state_text(following) if following else '全剧当前末端；未排后续时不视为剧情已结束')+'</p>')
    actual = item.get('prompt') if item else None
    rows.append(details('实际完整请求／提示词'+('（尚未编译就绪）' if actual is None else ''), '<pre>'+esc(actual or '请求待准备；缺项见内部诊断。')+'</pre>'))
    if item:
        rows.append(details('OSS执行核对：实际引用、哈希与请求字段', '<pre>'+esc(value_text(item))+'</pre>', 'execution-only'))
    diagnostic = {k:group.get(k) for k in ('review','uses','directing_notes') if group.get(k)}
    diagnostic['shot_states'] = [{k:s.get(k) for k in ('id','state_in','state_out','entry_state','exit_state','entry_bridge','events','timing') if k in s} for s in shots]
    rows.append(details('内部诊断与连续性依据', '<pre>'+esc(value_text(diagnostic))+'</pre>'))
    rows.append('</div></article>')
    return ''.join(rows)


def build(project_path, out, profile_path=None, episodes=None):
    pp = Path(project_path)
    base, data, out = pp.parent, load(pp), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    archive = archive_current(out)
    source = coverage(data, base)
    diagnostics = list(source['issues'])
    try:
        requested = _requested_ids(episodes, source['requested_episodes'])
    except ValueError as exc:
        requested = []
        diagnostics.append({'id':'PREVIEW_SCOPE', 'actual':str(exc)})
    groups = data.get('groups', [])
    planned = [g for g in groups if _episode_ids(g)&set(requested)]
    requested_missing = sorted(set(requested)-(set(source['registered_episodes'])&set(source['grouped_episodes'])))
    scope = {'episode_spec':episodes or ('本次已确认范围' if source['scope_explicit'] else '全部原剧本／已登记范围'),
             'group_ids':[g.get('id') for g in planned], 'episodes':requested,
             'source_episodes':source['source_episodes'], 'missing_episodes':requested_missing}
    save(out/'run_scope.json', scope)
    compiled = compile_jobs(data, base)
    save(out/'compiled_jobs.json', compiled)
    try:
        cfg = load(profile_path) if profile_path else {}
    except (OSError,ValueError,TypeError) as exc:
        cfg = {}
        diagnostics.append({'id':'PROFILE_READ','object':'local_profile','actual':str(exc)})
    video = cfg.get('video', {})
    parameters = {k:v for k,v in video.items() if k in ('model','resolution','concurrency','verified_concurrency_limit','verified_max_images','verified_durations_seconds','max_price_per_attempt_yuan','poll_seconds','retry_seconds')}
    price = video.get('max_price_per_attempt_yuan')
    priced = isinstance(price,(int,float)) and not isinstance(price,bool)
    quote = {'run_scope':scope, 'parameters':parameters,
             'budget_yuan':cfg.get('authorization', {}).get('budget_yuan'),
             'selected_first_pass_cap_yuan':price*len(planned) if priced else None,
             'unit_price_cap_yuan':price if priced else None,
             'first_pass_cap_yuan':round(price*len(groups),6) if priced else None}
    save(out/'quote_snapshot.json',quote)
    try:
        timing = build_plan(data)
    except Exception as exc:
        timing = {'basis':'当前分镜尚不完整；不能声明时长估算通过','episodes':[],'groups':[],
                  'issues':[{'code':'TIMING_PREPARING','object':'project','message':str(exc)}]}
    save(out/'timing_plan.json',timing)
    local = _validation(data,base,'local')
    ready = _validation(data,base,'ready')
    local_issues, ready_issues = local.get('issues', []), ready.get('issues', [])
    if not profile_path or not video.get('model') or not video.get('resolution'):
        ready_issues.append({'id':'PROFILE_PENDING','object':'local_profile','actual':'执行API模型／规格尚未从所传本机配置核实；本地审阅仍可进行'})
    diagnostics.extend(local_issues)
    assets = {a.get('id'):a for a in data.get('assets', [])}
    bound_ids = {b.get('asset_id') for g in groups for b in g.get('bindings', [])}
    resources = {aid:image_resource(base,out,assets.get(aid,{})) for aid in bound_ids}
    present = sum(bool(r.get('present')) for r in resources.values())
    uploaded = sum(bool(assets.get(aid,{}).get('remote_url')) for aid in bound_ids)
    execution_ready = not ready_issues and not compiled['unprepared'] and source['complete']
    queue = _progress(data,base)
    readiness = _readiness(data,base)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    checks = {'source_digest':digest(data), 'issues':diagnostics,
              'prepared':execution_ready, 'local_issues':local_issues, 'execution_issues':ready_issues,
              'source_coverage':source, 'stage_queue':queue, 'readiness':readiness, 'media_calls':0, 'updated_at':stamp,
              'local_preview_created':True, 'execution_ready':execution_ready,
              'progress':{'bound_assets':len(bound_ids),'local_images':present,'remote_addresses':uploaded,
                          'compiled_groups':len(compiled['items']),'groups':len(groups)}}
    save(out/'preview_checks.json',checks)
    ep_meta = {}
    for episode in data.get('episodes',[]):
        try:ep_meta[int(episode['id'])] = episode
        except (ValueError,TypeError,KeyError):pass
    all_ep_ids = sorted(set(source['source_episodes'])|set(ep_meta)|{ep for g in groups for ep in _episode_ids(g)})
    sidebar = '<a href="#overview">制作总览<small>阶段、范围、进度、版本</small></a>'
    for ep in all_ep_ids:
        count = sum(ep in _episode_ids(g) for g in groups)
        sidebar += '<a href="#ep-'+str(ep)+'">第'+str(ep)+'集 '+esc(ep_meta.get(ep,{}).get('title','未登记'))+'<small>'+str(count)+'组'+(' · 待排' if not count else '')+'</small></a>'
    mode = data.get('settings',{}).get('asset_review_mode','joint')
    mode_label = {'manual':'人工审阅','agent':'智能体审阅','joint':'人工与智能体共同审阅'}.get(mode,'待用户选择')
    stage = data.get('workflow',{}).get('stage') or data.get('workflow',{}).get('phase') or ('可进行执行核对' if execution_ready else '准备中 · 可本地审阅')
    model = video.get('model') if profile_path else None
    heading = '<header id="overview"><h1>'+esc(data.get('title',data.get('project_id','项目'))) +' · 跑前预览</h1><p class="lead">当前版本 '+esc(data.get('version','待登记'))+' · 更新时间 '+esc(stamp)+'</p></header>'
    heading += _entry_links(data,base)
    heading += '<div class="views" role="group" aria-label="预览视图"><label for="view-local">本地审阅</label><label for="view-execution">OSS执行核对</label></div>'
    heading += '<div class="stats">'+''.join('<div class="stat"><b>'+esc(a)+'</b><span>'+esc(b)+'</span></div>' for a,b in ((stage,'当前制作阶段'),(str(len(all_ep_ids))+' 集','原范围／登记合并展示'),(str(len(groups))+' 组','完整30秒请求'),(str(present)+' / '+str(len(bound_ids)),'本地参考图'),(str(uploaded)+' / '+str(len(bound_ids)),'已登记远端地址')))+'</div>'
    heading += '<section class="panel" id="readiness"><h2>准备状态：'+esc(readiness.get('label','准备中'))+'</h2><p>残血：全剧文本完成、图片未齐；半血：图片已生成，仍待审图／修图；满血：AI问题汇总和用户意见已处理，复核完成。文件数量只表示产出进度。</p>'+details('当前事实与复核依据','<pre>'+esc(value_text(readiness))+'</pre>')+'</section>'
    heading += '<div class="panel"><p><b>原剧本范围：</b>'+esc(_range_text(source['source_episodes']))+'；独立基准 '+esc('已核实' if source['basis_status']=='verified' else '待核实')+'</p><p><b>本次拟运行：</b>'+esc(_range_text(requested))+'；已排：'+esc(_range_text(source['grouped_episodes']))+'；全剧缺口：'+esc(_range_text(source['missing']) if source['missing'] else '无已知缺集')+'</p><p><b>审阅方式：</b>'+esc(mode_label)+'；审批状态以实际确认记录为准。</p><p class="local-only">本地页可在文字、图片、API或上传尚未完成时审阅。原图可点开；尚缺内容按真实状态标出。</p><p class="execution-only"><b>执行核对：</b>'+('当前结构检查可进入后续执行门槛' if execution_ready else '尚未就绪，请查看折叠诊断')+'；远端地址仅为登记状态，未在此页联网验证。</p><p>本面板只更新本地文件。打开或刷新不会上传、试跑或提交媒体请求。</p></div>'
    if not source['complete']:
        heading += '<p class="warn">原范围与本次覆盖尚未全部核实：全剧待排 '+esc(_range_text(source['missing']) if source['missing'] else '无已知缺集')+'；本次待排 '+esc(_range_text(requested_missing) if requested_missing else '无已知缺集')+'。原范围待核实时不能宣称全剧完成。</p>'
    heading += details('制作规格、声音与阶段记录','<pre>'+esc(value_text({'settings':data.get('settings',{}),'workflow':data.get('workflow',{}),'source_coverage':source}))+'</pre>')
    queue_state = {'not_initialized':'未初始化','active':'进行中','blocked':'待解决问题','completed':'台账已完成'}.get(queue.get('state'),queue.get('state','未初始化'))
    queue_stage = {'screenplay':'全剧剧本','storyboard_assets':'全剧分镜与资产表','images':'全量资产图','prevideo_preview':'跑前预览','awaiting_review':'图片审阅与修图'}.get(queue.get('stage'),queue.get('stage') or '待准备')
    heading += '<section class="panel" id="stage-progress"><h2>全剧阶段队列</h2><p>状态：'+esc(queue_state)+' · 当前阶段：'+esc(queue_stage)+' · 全剧分母：'+esc(source['source_count'] if source['source_count'] else '待核实')+' 集</p><p>台账批次总量 '+esc(queue.get('total','待初始化'))+'；已完成 '+esc(queue.get('completed',0))+'；运行中 '+esc(queue.get('running',0))+'；失败 '+esc(queue.get('failed',0))+'；阻塞 '+esc(queue.get('blocked',0))+'</p>'+details('下一批、游标与各阶段实际进度','<pre>'+esc(value_text(queue))+'</pre>')+'</section>'
    heading += details('执行模型、价格与本次参数（仅来自所传本机配置）','<p>模型：'+esc(model if model else '待传入本机配置；项目声明不视为当前API配置')+'；费用只按参数快照计算，不表示已扣费。</p><pre>'+esc(value_text(quote))+'</pre>','execution-only')
    heading += details('时长估算与逐集分组',render_summary(timing))
    heading += _text_materials(data,base)
    all_shots = [s for g in groups for s in g.get('shots', [])]
    positions = {id(s):i for i,s in enumerate(all_shots)}
    items = {item['id']:item for item in compiled['items']}
    body = [heading]
    for ep in all_ep_ids:
        ep_groups = [g for g in groups if ep in _episode_ids(g)]
        meta = ep_meta.get(ep,{})
        body.append('<section class="ep" id="ep-'+str(ep)+'"><h2>第'+str(ep)+'集 '+esc(meta.get('title','未登记'))+'</h2><p class="meta">'+esc(meta.get('ending') or meta.get('cliffhanger') or meta.get('end_state') or '集尾落点待登记')+'</p>')
        if not ep_groups:
            body.append('<p class="warn">本集尚未准备生成组。原剧本集号保留在此，不能据此省略覆盖缺口。</p>')
        for group in ep_groups:
            shots = group.get('shots',[])
            selected = _selected_ids(group,ep)
            used_shots = [shot for shot in shots if shot.get('id') in selected]
            first_pos = positions[id(used_shots[0])] if used_shots else 0
            last_pos = positions[id(used_shots[-1])] if used_shots else -1
            previous = all_shots[first_pos-1] if first_pos>0 else None
            following = all_shots[last_pos+1] if last_pos+1<len(all_shots) else None
            body.append(_group_card(data,group,ep,assets,resources,previous,following,items.get(group.get('id'))))
        body.append('</section>')
    if not all_ep_ids:
        body.append('<section class="panel"><h2>分集与分镜准备中</h2><p>尚未登记集号或生成组。保存分集与镜头后刷新此同一入口即可查看。</p></section>')
    body.append(details('本地结构／一致性问题（'+str(len(local_issues))+'）','<pre>'+esc(value_text(local))+'</pre>'))
    body.append(details('OSS执行检查与未编译请求（'+str(len(ready_issues))+'）','<pre>'+esc(value_text({'validation':ready,'unprepared':compiled['unprepared']}))+'</pre>','execution-only'))
    body.append('<p class="foot">本页共用同一分镜DOM与内容哈希图片文件；可离线打开。更新时间不代表内容已获批准。历史页面与参数快照保存在 history/，实际媒体请求次数：0。</p>')
    (out/PANEL).write_text(document('跑前预览 · '+str(data.get('project_id','项目')),sidebar,''.join(body)),encoding='utf-8')
    return {'episodes':len(data.get('episodes',[])), 'groups':len(groups), 'issues':len(diagnostics),
            'output':str(out), 'panel':str(out/PANEL), 'prepared':execution_ready,
            'source_coverage':source, 'archive':str(archive) if archive else None, 'media_calls':0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='刷新同一个本地跑前面板；不上传、不生成媒体')
    parser.add_argument('project'); parser.add_argument('out'); parser.add_argument('--profile'); parser.add_argument('--episodes')
    args = parser.parse_args()
    print(json.dumps(build(args.project,args.out,args.profile,args.episodes),ensure_ascii=False))
