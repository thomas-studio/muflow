"""Check declared states and events; source/spatial semantics still need an actual review."""
import copy,json
from core import digest

DOORS={'front_left':'left_front','front_right':'right_front','rear_left':'left_rear','rear_right':'right_rear'}

def leaves(x,p=''):
    if isinstance(x,dict):
        if not x:return {p:{}}
        return {k:v for a,b in x.items() for k,v in leaves(b,p+'/'+a).items()}
    return {p:x}

def events_match(before,after,events):
    b,a=leaves(before),leaves(after);changed={p for p in set(b)|set(a) if b.get(p)!=a.get(p)}
    named=[]
    for e in events:
        if not e.get('action') or not e.get('description') or not isinstance(e.get('changes'),list):return False
        for c in e['changes']:
            p=c.get('path');named.append(p)
            if p not in changed or c.get('before')!=b.get(p) or c.get('after')!=a.get(p):return False
    return len(named)==len(set(named)) and set(named)==changed

def audit(project,groups,stage='ready'):
    errors=[];all_assets={a['id']:a for a in project.get('assets',[])};entities=project.get('entities',{})
    def fail(code,obj,msg):errors.append((code,obj,msg))
    if not project.get('adaptation_review',{}).get('evidence') or project.get('adaptation_review',{}).get('status')!='通过':
        fail('ADAPTATION_REVIEW','project','缺少短剧适配性、时间跳转和信息表达的实际复核记录')
    if not entities:fail('ENTITY_REGISTRY','project','必须登记人物身份与载具的稳定实例ID')
    allshots=[s for g in project.get('groups',[]) for s in g.get('shots',[])];previous=None
    selected={s['id'] for g in groups for s in g.get('shots',[])}
    for s in allshots:
        sid=s['id'];sin=s.get('state_in');sout=s.get('state_out')
        if sid not in selected:previous=s;continue
        if not isinstance(sin,dict) or not isinstance(sout,dict) or not sin.get('entities') or not sout.get('entities'):
            fail('STATE_REQUIRED',sid,'需有结构化进入/离开状态');previous=s;continue
        if previous and previous.get('state_out')!=sin:
            if not events_match(previous.get('state_out',{}),sin,s.get('entry_bridge',[])):
                fail('BOUNDARY_STATE',sid,'上镜末态与本镜初态不一致，缺少逐项可见衔接或明确转场依据')
        if not events_match(sin,sout,s.get('events',[])):fail('STATE_EVENTS',sid,'所有状态变化必须有匹配前后值的事件，不得无动作跳变')
        for label,state in [('in',sin),('out',sout)]:
            es=state.get('entities',{});occupied={}
            for eid,e in es.items():
                if eid not in entities:fail('UNKNOWN_ENTITY',sid,eid)
                if not e.get('where'):fail('ENTITY_LOCATION',sid,eid+' 缺少世界位置')
                holder=e.get('holder')
                if holder:
                    if holder not in es or es[holder].get('where')!=e.get('where'):fail('CHILD_HOLDER',sid,eid+' 的抱持者不存在或不在同处')
                    if e.get('seat'):fail('CHILD_HOLDER',sid,eid+' 不能同时被抱持和独占座位')
                    seen={eid};cur=holder
                    while cur in es:
                        if cur in seen:fail('HOLDER_CYCLE',sid,'抱持关系成环');break
                        seen.add(cur);cur=es[cur].get('holder')
                seat=e.get('seat');where=e.get('where','')
                if seat:
                    if not where.startswith('vehicle:'):fail('SEAT_LOCATION',sid,eid+' 座位未指定车辆')
                    key=(where,seat)
                    if key in occupied:fail('SEAT_DUPLICATE',sid,eid+' 与 '+occupied[key]+' 占据同一座位')
                    occupied[key]=eid
                aid=e.get('reference_asset_id')
                if aid:
                    a=all_assets.get(aid)
                    if not a:fail('STATE_ASSET',sid,'未知状态素材 '+aid)
                    elif a.get('entity_id')!=eid or a.get('state_key')!=e.get('visual_state'):
                        fail('STATE_ASSET',sid,eid+' 的身份或可见状态与实际参考图不一致')
            visible=s.get('visible_entities',[]);ids=[entities.get(e,{}).get('identity_id',e) for e in visible]
            if len(visible)!=len(set(visible)) or len(ids)!=len(set(ids)):fail('DUPLICATE_PERSON',sid,'同镜出现重复身份实例')
            for eid in visible:
                if eid not in es:fail('VISIBLE_STATE',sid,eid+' 可见但缺少状态')
                elif entities.get(eid,{}).get('kind')=='person':
                    aid=es[eid].get('reference_asset_id')
                    if not aid or aid not in s.get('asset_ids',[]):fail('VISIBLE_REFERENCE',sid,eid+' 可见状态未绑定实际素材')
            if any(e not in visible for e in s.get('camera_subjects',[])):fail('CAMERA_SUBJECT',sid,'镜头主体不在可见人物清单')
        for e in s.get('events',[])+s.get('entry_bridge',[]):
            if e.get('action') in ('board','alight'):
                if e.get('door')!=DOORS.get(e.get('seat')) or not e.get('vehicle_id') or not e.get('entity_id'):
                    fail('VEHICLE_DOOR',sid,'上下车人物、载具、座次及车门不对应')
            if e.get('action')=='walk_around' and not e.get('route'):fail('WALK_AROUND',sid,'绕车需指明车前或车后的路径')
            if e.get('action') in ('heal','new_injury') and not e.get('source_evidence'):
                fail('INJURY_CAUSE',sid,'新增伤/愈合必须有情节证据，遮住伤不等于愈合')
        if stage=='ready':
            for fx in s.get('sound_effects',[]):
                if not isinstance(fx,dict) or not fx.get('physical_source') or not fx.get('purpose'):
                    fail('SOUND_PURPOSE',sid,'物理音效须写实际声源和叙事必要性')
        previous=s
    return errors
