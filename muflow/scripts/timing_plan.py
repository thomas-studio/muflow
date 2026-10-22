"""Fixed 30-second requests, with story-driven episode duration estimates.
No media calls, script rewriting, automatic shot splitting or measured-duration claims.
"""
import argparse,html,json,math
from core import load,save
from speech_metrics import spoken_count

REQUEST_SECONDS=30
DEFAULT_SPEECH_RATE=4.5
DEFAULT_ACTION_SECONDS=2.2
EPS=1e-7

class TimingError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)

def number(value,label,positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or (positive and value<=0):
        raise TimingError('TIMING_VALUE',label+' must be a finite '+('positive' if positive else 'non-negative')+' number')
    return float(value)

def timing_settings(project):
    s=project.get('settings',{})
    group=number(s.get('group_target_seconds',REQUEST_SECONDS),'group_target_seconds',True)
    if group!=REQUEST_SECONDS:raise TimingError('REQUEST_SECONDS','每次真实视频请求固定30秒，不再次询问或由代理改为其他时长')
    if s.get('episode_duration_mode','story_driven')!='story_driven' or s.get('episode_target_seconds') is not None:
        raise TimingError('EPISODE_DURATION_MODE','单集按剧情自动计算；移除旧统一episode_target_seconds，不把150秒或300秒设为全剧目标')
    return {'episode_duration_mode':'story_driven','group_target_seconds':REQUEST_SECONDS,
        'speech_chars_per_second':number(s.get('speech_chars_per_second',DEFAULT_SPEECH_RATE),'speech_chars_per_second',True),
        'action_seconds_per_beat':number(s.get('action_seconds_per_beat',DEFAULT_ACTION_SECONDS),'action_seconds_per_beat',True)}

def minimum_groups(seconds):
    return max(0,math.ceil(number(seconds,'episode estimate')/REQUEST_SECONDS-EPS))

def estimate_shot(shot,settings):
    t=shot.get('timing')
    if not isinstance(t,dict) or not any(k in t for k in ('action_beats','action_seconds')):
        raise TimingError('TIMING_MISSING','镜头须登记timing及动作拍/动作秒数依据，不能只以台词推断全部时长')
    if t.get('padding_only'):raise TimingError('EMPTY_PADDING','禁止为凑30秒增加无意义空镜或重复反应')
    text=''.join(f.get('spoken_text',f.get('text','')) for f in shot.get('speech',[]))
    chars=spoken_count(text);speech=chars/settings['speech_chars_per_second']
    if 'action_seconds' in t:
        action=number(t['action_seconds'],'action_seconds');beats=None
    else:
        beats=number(t['action_beats'],'action_beats')
        if not beats.is_integer():raise TimingError('TIMING_VALUE','action_beats须为非负整数')
        action=beats*settings['action_seconds_per_beat']
    if action>0 and not t.get('action_basis'):raise TimingError('TIMING_BASIS','动作估算缺少实际动作及来源依据')
    pause=number(t.get('pause_seconds',0),'pause_seconds');transition=number(t.get('transition_seconds',0),'transition_seconds')
    for value,key in ((pause,'pause_basis'),(transition,'transition_basis')):
        if value>0 and not t.get(key):raise TimingError('TIMING_BASIS',key+' required')
    overlap=number(t.get('overlap_seconds',0),'overlap_seconds')
    if overlap>min(speech,action)+EPS:raise TimingError('TIMING_OVERLAP','对白与动作重叠不能超过两者中较短项')
    if overlap>0 and not t.get('overlap_evidence'):raise TimingError('TIMING_OVERLAP','重叠扣时须说明哪段对白与哪个动作真实同时发生')
    total=speech+action+pause+transition-overlap
    if total<=EPS:raise TimingError('EMPTY_PADDING','无对白也无有依据动作的镜头不能占时')
    return {'spoken_characters':chars,'speech_seconds_estimate':speech,'action_beats':beats,'action_seconds_estimate':action,
        'pause_seconds_estimate':pause,'transition_seconds_estimate':transition,'overlap_seconds_estimate':overlap,
        'estimated_seconds':total,'basis':t}

def plan_atoms(atoms,settings):
    """Pack already approved indivisible dramatic atoms in order, without splitting text.
    Long atoms go back for a semantic split at complete dialogue/action boundaries.
    """
    result=[];current=[];used=0
    for atom in atoms:
        sec=estimate_shot(atom,settings)['estimated_seconds']
        if sec>REQUEST_SECONDS+EPS:raise TimingError('ATOM_TOO_LONG','单个完整动作/台词单元超30秒，先按语义拆分并回拼核对，禁止截断或加速：'+str(atom.get('id')))
        if current and used+sec>REQUEST_SECONDS+EPS:
            result.append({'atom_ids':current,'estimated_seconds':used,'expected_seconds':REQUEST_SECONDS,'headroom_seconds_estimate':REQUEST_SECONDS-used});current=[];used=0
        current.append(atom['id']);used+=sec
    if current:result.append({'atom_ids':current,'estimated_seconds':used,'expected_seconds':REQUEST_SECONDS,'headroom_seconds_estimate':REQUEST_SECONDS-used})
    return result

def build_plan(project,groups=None):
    groups=project.get('groups',[]) if groups is None else groups;issues=[];rows=[];episode_rows={}
    def fail(code,obj,msg):issues.append({'code':code,'object':obj,'message':msg})
    try:settings=timing_settings(project)
    except TimingError as e:return {'basis':'估算，非试读或成片实测','episodes':[],'groups':[],'issues':[{'code':e.code,'object':'settings','message':str(e)}]}
    for g in groups:
        gid=g['id'];entries=[];estimated=0;complete=True;issue_start=len(issues)
        try:
            if number(g.get('expected_seconds'),'expected_seconds',True)!=REQUEST_SECONDS:fail('REQUEST_SECONDS',gid,'生成组和真实请求必须为30秒，duration_exception不能绕过')
            allocated=sum(number(s.get('seconds'),'shot seconds',True) for s in g.get('shots',[]))
            if abs(allocated-REQUEST_SECONDS)>EPS:fail('DURATION',gid,'逐镜分配秒数合计必须精确30秒')
        except TimingError as e:allocated=0;fail(e.code,gid,str(e))
        for s in g.get('shots',[]):
            try:
                metrics=estimate_shot(s,settings);sec=number(s.get('seconds'),'shot seconds',True);need=metrics['estimated_seconds'];estimated+=need
                if need>sec+EPS:fail('TIMING_CAPACITY',s['id'],'对白、动作、停顿和转场估算超出本镜分配；自动调整镜界或增加组，不让用户选择统一单集时长')
                if sec>need+EPS and not s['timing'].get('allocation_basis'):fail('TIMING_HEADROOM',s['id'],'额外分配时间须说明有剧情作用的表演、动作收尾或剪辑余量依据，不能自动填空镜')
                entries.append({'shot_id':s['id'],'allocated_seconds':sec,**metrics})
            except TimingError as e:complete=False;fail(e.code,s['id'],str(e))
        if estimated>REQUEST_SECONDS+EPS:fail('GROUP_CAPACITY',gid,'整组剧情估算超过30秒，需重新分配或自动增组')
        row={'id':gid,'episode':g['episode'],'estimated_seconds':estimated,'complete_estimate':complete,'allocated_seconds':allocated,
             'headroom_seconds_estimate':REQUEST_SECONDS-estimated,'shots':entries,'status':'不通过' if len(issues)>issue_start else '通过'};rows.append(row)
        uses=g.get('uses') or [{'episode':g['episode'],'shot_ids':[s['id'] for s in g.get('shots',[])]}]
        for use in uses:
            ep=int(use['episode']);r=episode_rows.setdefault(ep,{'episode':ep,'group_ids':[],'estimated_seconds':0,'allocated_usage_seconds':0,'complete_estimate':True,'group_checks_passed':True})
            r['group_ids'].append(gid);ids=set(use.get('shot_ids',[]));r['estimated_seconds']+=sum(x['estimated_seconds'] for x in entries if x['shot_id'] in ids)
            r['allocated_usage_seconds']+=sum(x['allocated_seconds'] for x in entries if x['shot_id'] in ids);r['complete_estimate']&=complete;r['group_checks_passed']&=row['status']=='通过'
    for ep,r in sorted(episode_rows.items()):
        r['group_ids']=list(dict.fromkeys(r['group_ids']));r['planned_groups']=len(r['group_ids']);r['minimum_groups_by_estimate']=minimum_groups(r['estimated_seconds'])
        r['generated_seconds']=REQUEST_SECONDS*r['planned_groups'];r['headroom_seconds_estimate']=r['allocated_usage_seconds']-r['estimated_seconds']
        r['status']='不通过' if not r['complete_estimate'] or not r['group_checks_passed'] or r['headroom_seconds_estimate']<-EPS else '通过'
    return {'basis':'按去标点可发声字数、动作拍/具体秒数、停顿、转场及有证据的重叠关系估算；非试读或成片实测',
        'settings':settings,'episodes':list(episode_rows.values()),'groups':rows,'issues':issues,
        'total_requests':len(groups),'generated_seconds':REQUEST_SECONDS*len(groups)}

def assert_valid_plan(project):
    report=build_plan(project)
    if report['issues']:raise ValueError('Timing plan invalid: '+str(report['issues'][0]))
    return report

def render_summary(report):
    esc=lambda x:html.escape(str(x));out=['<h2>逐集30秒分组与时长估算</h2><p>'+esc(report['basis'])+'。集长为计算结果，不是统一设置；余量不等于空镜。</p><table border="1" cellpadding="6"><tr><th>集</th><th>剧情估算秒</th><th>算术最低组数</th><th>已排组数</th><th>生成秒</th><th>本集使用秒</th><th>估算余量秒</th></tr>']
    for r in report.get('episodes',[]):out.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in (r['episode'],round(r['estimated_seconds'],2),r['minimum_groups_by_estimate'],r['planned_groups'],r['generated_seconds'],r['allocated_usage_seconds'],round(r['headroom_seconds_estimate'],2)))+'</tr>')
    out.append('</table><p>最低组数只是估算下限；台词/动作切点、逐组容量和必要转场可能使实际组数增加。</p><details><summary>逐镜依据、余量及检查问题</summary><pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre></details>')
    return ''.join(out)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='本地估算逐集30秒分组，不生成媒体');ap.add_argument('project');ap.add_argument('--out');ap.add_argument('--pack-atoms',action='store_true');a=ap.parse_args();p=load(a.project)
    report=build_plan(p)
    if a.pack_atoms:
        s=timing_settings(p);report['suggested_partitions']=[{'episode':ep['id'],'groups':plan_atoms([shot for g in p.get('groups',[]) if g['episode']==ep['id'] for shot in g.get('shots',[])],s)} for ep in p.get('episodes',[])]
    if a.out:save(a.out,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(report['issues']))
