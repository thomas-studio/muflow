"""Review evidence tied to actual subject content; no script can prove human judgement."""
from core import digest,sha,resolve

MODES=('manual','agent','joint')
MODE_NAMES={'manual':'人工验收','agent':'智能体验收','joint':'人工＋智能体验收'}

def mode(p):
    value=p.get('settings',{}).get('asset_review_mode','joint')
    if value not in MODES:raise ValueError('Choose asset_review_mode: manual, agent or joint at project setup')
    return value

def asset_subject(p,a):
    parents={x['id']:x for x in p.get('assets',[])}
    return digest({k:a.get(k) for k in ('id','entity_id','state_key','mode','parent_id','prompt','sha256')}|
                  {'parent_sha256':parents.get(a.get('parent_id'),{}).get('sha256')})

def group_subject(p,g):
    scenes={s['id']:s for s in p.get('scenes',[])};dialogues={d['id']:d for d in p.get('dialogues',[])}
    return digest({'id':g['id'],'shots':g.get('shots',[]),'bindings':g.get('bindings',[]),
       'scenes':[scenes.get(s.get('scene_id')) for s in g.get('shots',[])],
       'dialogues':[dialogues.get(f['id']) for s in g.get('shots',[]) for f in s.get('speech',[])]})

def _check_rows(review,expected_subject,required,shot_ids=None):
    if not isinstance(review,dict) or review.get('status')!='通过':return '审查尚未通过'
    if review.get('subject_digest')!=expected_subject:return '审查未绑定当前内容或内容已改变'
    if review.get('performed_by') not in ('agent','human') or not review.get('checked_at'):return '缺少实际审查者和时间'
    if not review.get('evidence'):return '缺少审查依据'
    rows=review.get('checks')
    if not isinstance(rows,list) or not rows:return '不能只填模板通过，需具体检查记录'
    seen=set();covered=set()
    for row in rows:
        if not isinstance(row,dict) or not row.get('rule_id') or not row.get('expected') or not row.get('observed') or not row.get('evidence'):
            return '检查记录需列规则、预期、实际观察和证据'
        if row.get('result')!='通过':return '存在未通过或未执行的检查项'
        seen.add(row['rule_id']);covered.update(row.get('shot_ids',[]))
    if not set(required)<=seen:return '尚未覆盖要求的审查维度：'+','.join(sorted(set(required)-seen))
    if shot_ids and not set(shot_ids)<=covered:return '部分镜头没有实际审查记录'
    return None

def visual_error(p,a):
    required={'identity_stage','appearance'}
    if a.get('kind') in ('scene','vehicle'):required.add('space')
    if a.get('purpose')=='identity_sheet':required.add('video_reference')
    return _check_rows(a.get('review'),asset_subject(p,a),required)

def semantic_error(p,g):
    required={'story_continuity','space'}
    if any(s.get('speech') for s in g.get('shots',[])):required.add('speaker_channel')
    if any(s.get('sound_effects') for s in g.get('shots',[])):required.add('physical_sound')
    return _check_rows(g.get('review'),group_subject(p,g),required,[s['id'] for s in g.get('shots',[])])

def human_asset_record(p,a):
    approval=p.get('workflow',{}).get('image_approval',{})
    for item in approval.get('items',[]):
        if item.get('asset_id')==a['id'] and item.get('subject_digest')==asset_subject(p,a) and item.get('sha256')==a.get('sha256'):
            return item
    return None

def require_human_asset(p,a):
    item=human_asset_record(p,a)
    if not item:raise ValueError('Generated images await user review: '+a['id'])
    from review_evidence import verify_record
    verify_record(item,'images')
    return item

def _no_open_feedback(p,a):
    for feedback in p.get('workflow',{}).get('asset_feedback',[]):
        affected=set(feedback.get('asset_ids',[]))|{feedback.get('asset_id')}
        if a['id'] in affected and feedback.get('status') not in ('已解决','通过','resolved'):
            raise ValueError('User image feedback unresolved: '+a['id'])
        if a['id'] in affected and not (feedback.get('evidence') and feedback.get('recheck')):raise ValueError('User feedback resolution evidence missing: '+a['id'])
    for issue in a.get('review',{}).get('issues',[]):
        if issue.get('status') not in ('已解决','通过','resolved') or not issue.get('recheck'):raise ValueError('Image review issue unresolved: '+a['id'])

def asset_ready(p,a):
    _no_open_feedback(p,a)
    selected=mode(p)
    if selected in ('manual','joint'):require_human_asset(p,a)
    if selected in ('agent','joint'):
        error=visual_error(p,a)
        if error:raise ValueError('Actual image review required: '+a['id']+'；'+error)
    return True

def mother_ready(p,base,a):
    _no_open_feedback(p,a)
    f=resolve(base,a['local_path'])
    if sha(f)!=a.get('sha256'):raise ValueError('Mother changed')
    if any(r.get('asset_id')==a['id'] and r.get('identity_locked') and r.get('sha256')==a.get('sha256') for r in p.get('input_assets',[])):
        return True
    try:require_human_asset(p,a);return True
    except ValueError:pass
    if visual_error(p,a) is None:return True
    raise ValueError('Mother requires an actual reviewed or user-adopted image before editing')

def readiness(p,base):
    """Summarize recorded readiness, never inspect unseen pictures or infer approval."""
    from asset_gallery import required_ids
    from source_coverage import coverage
    from workflow_gate import text_manifest
    evidence=[];scope=coverage(p,base);text_complete=False
    try:text_manifest(p,base);text_complete=bool(scope['full_complete'])
    except (KeyError,OSError,ValueError) as exc:evidence.append({'object':'text','status':'待复核','reason':str(exc)})
    assets={a['id']:a for a in p.get('assets',[])};needed=required_ids(p)
    from pathlib import Path
    from core import load
    queue=Path(base)/'stage_queue.json'
    if queue.is_file():
        q=load(queue)
        if q.get('project_id')==p.get('project_id'):needed=set(q.get('stages',{}).get('images',{}).get('items',{}))
    present=0;ready=0
    for aid in needed:
        a=assets.get(aid,{})
        try:
            if not a.get('local_path') or sha(resolve(base,a['local_path']))!=a.get('sha256'):raise ValueError('图片未生成或哈希不匹配')
            present+=1;asset_ready(p,a);ready+=1
        except (OSError,ValueError,KeyError) as exc:evidence.append({'object':aid,'status':'待复核','reason':str(exc)})
    semantic=[{'object':g['id'],'status':'待复核','reason':err} for g in p.get('groups',[]) if (err:=semantic_error(p,g))]
    evidence.extend(semantic)
    feedback=p.get('workflow',{}).get('asset_feedback',[])
    user_resolved=all(f.get('status') in ('已解决','通过','resolved') and f.get('evidence') and f.get('recheck') for f in feedback)
    issues_resolved=all(i.get('status') in ('已解决','通过','resolved') and i.get('recheck') for a in p.get('assets',[]) for i in a.get('review',{}).get('issues',[]))
    local_valid=False
    if text_complete:
        try:
            from validate_project import validate
            local_valid=not validate(p,base,stage='local')['issues']
        except Exception as exc:evidence.append({'object':'local_structure','status':'待复核','reason':str(exc)})
    all_reviews=bool(needed) and ready==len(needed) and not semantic
    full=text_complete and local_valid and all_reviews and issues_resolved and user_resolved
    label='满血' if full else '半血' if present else '残血' if text_complete else '准备中'
    return {'label':label,'text_complete':text_complete,'images_present':present,'image_total':len(needed),
       'reviews_complete':all_reviews,'issues_resolved':issues_resolved,'user_feedback_resolved':user_resolved,
       'local_structure_complete':local_valid,'source_full_complete':scope['full_complete'],'evidence':evidence,
       'meaning':'依据本地实际文件、全剧文本和选定验收方式记录；不代表OSS可执行，不授权视频'}
