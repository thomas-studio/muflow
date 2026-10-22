"""Deterministic checks; does not claim source semantics or image quality were checked."""
from __future__ import annotations
import argparse, collections, json, re
from decimal import Decimal
from pathlib import Path
from core import load,resolve,sha,prompt,select,issue,save

def validate(project,base,groups=None,stage='ready'):
    problems=[]; checks=[]; groups=groups if groups is not None else project.get('groups',[])
    def fail(code,obj,msg,ev=''):
        row=issue(code,obj,msg,ev);row.update(version=project.get('version'),rule_id=code,basis='当前已登记源句/状态/实际文件与规范字段');problems.append(row)
    def unique(items,key,label):
        ids=[str(x.get(key,'')) for x in items]
        if '' in ids or len(ids)!=len(set(ids)): fail('UNIQUE',label,'Missing or duplicate IDs')
    for k in ('episodes','scenes','dialogues','assets','groups'): unique(project.get(k,[]),'id',k)
    eps={int(e['id']):e for e in project.get('episodes',[])}
    scenes={s['id']:s for s in project.get('scenes',[])}
    ds={d['id']:d for d in project.get('dialogues',[])}
    assets={a['id']:a for a in project.get('assets',[])}
    if project.get('schema_version')==2:
        from audit_project_network import audit as audit_network
        for row in audit_network(base)['issues']:fail('UNCONTROLLED_NETWORK_SCRIPT',row['file'],row['reason'])
        from source_coverage import coverage
        source_check=coverage(project,base)
        if stage=='ready' and not source_check['complete']:fail('SOURCE_SCOPE','project','独立原稿集数与本次全量覆盖不完整',str(source_check))
        from timing_plan import build_plan
        for error in build_plan(project,groups)['issues']:fail(error['code'],error['object'],error['message'])
        from continuity import audit
        for code,obj,msg in audit(project,groups,stage):fail(code,obj,msg)
        for k in ('onscreen_text','subtitles','background_music','ambient_bed'):
            if project.get('settings',{}).get(k) is not False:fail('AUDIO_VISUAL_POLICY','settings',k+' must explicitly be false')
    source_ids=set()
    if project.get('source_path'):
        try: source_ids={p['id'] for p in load(resolve(base,project['source_path']))['paragraphs']}
        except Exception as e: fail('SOURCE','source_path',str(e))
    else: fail('SOURCE','project','source_path is required')
    all_fragments=collections.defaultdict(list); all_sids=[]
    for g in project.get('groups',[]):
        for s in g.get('shots',[]):
            all_sids.append(s.get('id'))
            for f in s.get('speech',[]): all_fragments[f.get('id')].append(f)
    if len(all_sids)!=len(set(all_sids)) or None in all_sids: fail('SHOT_ID','shots','Shot IDs must be globally unique')
    selected_d={f.get('id') for g in groups for s in g.get('shots',[]) for f in s.get('speech',[])}
    observed=list(dict.fromkeys(f['id'] for g in groups for s in g.get('shots',[]) for f in s.get('speech',[])))
    expected=[d['id'] for d in project.get('dialogues',[]) if d['id'] in selected_d]
    if observed!=expected:fail('DIALOGUE_ORDER','selected groups','Speech units out of approved source order')
    for did in selected_d:
        if did not in ds: fail('DIALOGUE',str(did),'Unknown dialogue');continue
        d=ds[did]; fs=all_fragments[did]
        if ''.join(f.get('text','') for f in fs)!=d.get('text'): fail('DIALOGUE_TEXT',did,'Fragments do not reconstruct approved dialogue')
        for f in fs:
            if f.get('speaker')!=d.get('speaker') or f.get('channel')!=d.get('channel'):
                fail('SPEAKER_CHANNEL',did,'Speaker/channel mismatch',str(f))
            if not f.get('emotion'): fail('EMOTION',did,'Missing performance direction')
        refs=d.get('source_ids',[])
        if not refs or any(x not in source_ids for x in refs): fail('DIALOGUE_SOURCE',did,'Missing/invalid source anchors')
        if not d.get('evidence'): fail('ATTRIBUTION',did,'Attribution evidence missing')
    if project.get('schema_version')==2:
        selected_eps={int(u['episode']) for g in groups for u in (g.get('uses') or [{'episode':g['episode']}])}
        for did,d in ds.items():
            if d.get('episode') not in eps:fail('DIALOGUE_EPISODE',did,'每条台词需登记已批准归集，避免整句漏出实际请求')
            elif int(d['episode']) in selected_eps and not all_fragments.get(did):fail('DIALOGUE_OMITTED',did,'所选集台词没有镜头及实际请求覆盖')
    if project.get('schema_version')!=2 and len(groups)==len(project.get('groups',[])):
        for did in ds:
            if not all_fragments.get(did): fail('DIALOGUE_OMITTED',did,'No shot covers this approved speech unit')
    for g in groups:
        gid=g['id']; ep=int(g['episode']); shots=g.get('shots',[]); use_eps={int(u['episode']) for u in g.get('uses',[])} or {ep}
        if g.get('uses'):
            ownership=[sid for u in g['uses'] for sid in u.get('shot_ids',[])]
            if sorted(ownership)!=sorted(s['id'] for s in shots):fail('USES',gid,'Each shot must belong to exactly one episode use')
            for u in g['uses']:
                total_use=sum((Decimal(str(s['seconds'])) for s in shots if s['id'] in u.get('shot_ids',[])),Decimal('0'))
                if int(u['episode']) not in eps or Decimal(str(u['end_second']))-Decimal(str(u['start_second']))!=total_use:fail('USES',gid,'Invalid episode interval or duration')
        if ep not in eps: fail('EPISODE',gid,'Unknown episode')
        if not shots: fail('SHOTS',gid,'No shots')
        try:
            total=sum((Decimal(str(s['seconds'])) for s in shots),Decimal('0'))
            if any(Decimal(str(s['seconds']))<=0 for s in shots) or total!=Decimal(str(g['expected_seconds'])):
                fail('DURATION',gid,'Shot seconds must be positive and sum exactly to expected_seconds')
        except Exception as e: fail('DURATION',gid,str(e))
        if project.get('schema_version')==2:
            target=project.get('settings',{}).get('group_target_seconds')
            if target not in (None,30) or g.get('expected_seconds')!=30:fail('REQUEST_SECONDS',gid,'每组对应一次真实30秒请求；不能以例外字段改时长或让用户重选')
        for s in shots:
            if project.get('schema_version')==2:
                from speech_metrics import estimate
                rate=project.get('settings',{}).get('speech_chars_per_second',4.5)
                try:
                    stats=estimate(''.join(f.get('spoken_text',f.get('text','')) for f in s.get('speech',[])),rate)
                    if stats['speech_seconds_estimate']>float(s['seconds']):fail('SPEECH_CAPACITY',s['id'],'按去标点口径的纯发声估计已超出镜头时长；不能异常加速凑秒')
                except ValueError as e:fail('SPEECH_RATE',s['id'],str(e))
                if not s.get('camera') or not s.get('action'):fail('SHOT_EXECUTION',s['id'],'需有实际镜头机位与动作，不能只填状态')
            sid=s.get('scene_id');sc=scenes.get(sid)
            owner=[u for u in g.get('uses',[]) if s['id'] in u.get('shot_ids',[])]
            if owner and sc and int(owner[0]['episode'])!=int(sc['episode']):fail('USES_SCENE',s['id'],'Use episode disagrees with scene')
            if not sc or int(sc.get('episode',-1)) not in use_eps: fail('SCENE',s.get('id',''), 'Unknown or wrong-episode scene')
            elif not sc.get('name') or not sc.get('time') or not sc.get('transition'):
                fail('SCENE_MARK',sid,'Scene name, time and transition/continuation required')
            if project.get('schema_version')==2 and sc:
                for fragment in s.get('speech',[]):
                    if fragment.get('id') in ds and ds[fragment['id']].get('episode')!=sc.get('episode'):fail('DIALOGUE_WRONG_EPISODE',fragment['id'],'实际镜头与已批准台词归集不一致')
            if not s.get('entry_state') or not s.get('exit_state'): fail('CONTINUITY',s.get('id',''),'Missing entry/exit state')
        if stage=='text': continue
        binds=g.get('bindings',[]); bound={b.get('asset_id') for b in binds}
        if not binds: fail('BINDINGS',gid,'No image references')
        if len({b.get('name') for b in binds})!=len(binds): fail('BINDINGS',gid,'Binding names ambiguous')
        for s in shots:
            for aid in s.get('asset_ids',[]):
                if aid not in bound: fail('SHOT_BINDING',s['id'],'Visible asset not bound: '+aid)
        try:
            raw=prompt(base,g)
            if project.get('schema_version')==2:
                from compose_requests import compose_group
                if raw!=compose_group(project,g):fail('REQUEST_DRIFT',gid,'实际请求词未由当前全部镜头和台词编译；不得只核对分镜表')
            found=[(a.strip(),int(n)) for a,n in re.findall(r'@([^=\r\n@]+)=图(\d+)',raw)]
            want=[(b['name'],i) for i,b in enumerate(binds,1)]
            if found!=want: fail('ANCHOR_ORDER',gid,'Prompt anchors must match reference order',str(found))
        except Exception as e: fail('PROMPT',gid,str(e))
        for b in binds:
            a=assets.get(b.get('asset_id'))
            if not a: fail('ASSET',gid,'Unknown bound asset');continue
            if stage not in ('ready','local'):continue
            try:
                p=resolve(base,a['local_path']);actual=sha(p)
                if actual!=a.get('sha256'): fail('ASSET_HASH',a['id'],'Image differs from approved hash')
            except Exception as e: fail('ASSET_FILE',a['id'],str(e));continue
            if stage=='ready' and project.get('schema_version')==2:
                if not a.get('remote_url') or a.get('remote_sha256')!=a.get('sha256') or not a.get('remote_reference_evidence'):fail('REMOTE_REFERENCE',a['id'],'实际远端引用缺少同版本哈希与传输核对证据')
            if stage=='ready':
                if project.get('schema_version')==2:
                    from review_state import asset_ready
                    try:asset_ready(project,a)
                    except ValueError as exc:fail('IMAGE_REVIEW',a['id'],str(exc))
                elif a.get('review',{}).get('status')!='通过' or not a.get('review',{}).get('evidence'):
                    fail('IMAGE_REVIEW',a['id'],'Actual image review evidence required')
            if project.get('schema_version')==2:
                if a.get('parent_id') and a.get('mode')!='edit':fail('EDIT_REQUIRED',a['id'],'有母图的派生/参考转换必须使用真实图片编辑')
            if a.get('mode')=='edit':
                parent=assets.get(a.get('parent_id')); audit=a.get('edit_audit',{})
                if not parent or audit.get('operation')!='image_edit' or not audit.get('endpoint') or audit.get('input_sha256')!=parent.get('sha256') or audit.get('input_asset_id')!=a.get('parent_id') or audit.get('input_count')!=1:
                    fail('EDIT_AUDIT',a['id'],'Real edit operation, endpoint and correct input hash required')
        if stage=='ready':
            if project.get('schema_version')==2:
                from review_state import semantic_error
                error=semantic_error(project,g)
                if error:fail('SEMANTIC_REVIEW',gid,'Actual source/continuity review required: '+error)
            else:
                review=g.get('review',{})
                if review.get('status')!='通过' or not review.get('evidence'):fail('SEMANTIC_REVIEW',gid,'Agent source/continuity review evidence required')
    import datetime
    checks.append({'executed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rule_id':'deterministic-v2','execution_status':'已执行','id':'STRUCTURE','object':'selected project groups','version':project.get('version'),
                   'status':'不通过' if problems else '通过','evidence':'Deterministic source/fragment/scene/asset checks',
                   'scope':'程序检查；不代表内容语义和实际图片已由脚本审阅'})
    return {'version':project.get('version'),'selected_groups':[g['id'] for g in groups], 'checks':checks,'issues':problems}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('--episodes');ap.add_argument('--stage',choices=['text','assets','local','ready'],default='ready');ap.add_argument('--out');a=ap.parse_args()
    p=Path(a.project);data=load(p);report=validate(data,p.parent,select(data,a.episodes),a.stage)
    if a.out:save(a.out,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(report['issues']))
