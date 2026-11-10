"""Fixed 30-second requests, with story-driven episode duration estimates.
No media calls, script rewriting, automatic shot splitting or measured-duration claims.
"""
import argparse,html,json,math
from core import load,save
from speech_metrics import spoken_count

REQUEST_SECONDS=30
DEFAULT_SPEECH_RATE=5.0
LEGACY_SPEECH_RATE=4.5
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
        raise TimingError('EPISODE_DURATION_MODE','单集按剧情自动计算，不统一设150秒或300秒')
    # Existing settings and their original snapshot stay intact until explicitly migrated.
    mode=s.get('timing_mode') or ('legacy' if 'action_seconds_per_beat' in s or 'speech_chars_per_second' in s else 'company-speech-ratio')
    if mode=='legacy':
        return {'episode_duration_mode':'story_driven','group_target_seconds':REQUEST_SECONDS,
          'speech_chars_per_second':number(s.get('speech_chars_per_second',LEGACY_SPEECH_RATE),'speech_chars_per_second',True),
          'action_seconds_per_beat':number(s.get('action_seconds_per_beat',DEFAULT_ACTION_SECONDS),'action_seconds_per_beat',True)}
    if mode!='company-speech-ratio':raise TimingError('TIMING_MODE','未知计时口径')
    low=number(s.get('non_speech_ratio_min',0.10),'non_speech_ratio_min')
    high=number(s.get('non_speech_ratio_max',0.15),'non_speech_ratio_max')
    target=number(s.get('non_speech_ratio_target',0.10),'non_speech_ratio_target')
    if not (0.10<=low<=target<=high<=0.15):raise TimingError('NON_SPEECH_RATIO','公司无对白预算须在10%—15%内')
    rate=number(s.get('speech_chars_per_second',DEFAULT_SPEECH_RATE),'speech_chars_per_second',True)
    out={'episode_duration_mode':'story_driven','group_target_seconds':REQUEST_SECONDS,
         'timing_mode':mode,'speech_chars_per_second':rate,'non_speech_ratio_min':low,
         'non_speech_ratio_max':high,'non_speech_ratio_target':target}
    if s.get('action_seconds_per_beat') is not None:
        out['action_seconds_per_beat']=number(s['action_seconds_per_beat'],'action_seconds_per_beat',True)
    return out

def minimum_groups(seconds):
    return max(0,math.ceil(number(seconds,'episode estimate')/REQUEST_SECONDS-EPS))

def estimate_shot(shot,settings):
    t=shot.get('timing')
    if not isinstance(t,dict) or not any(k in t for k in ('action_beats','action_seconds')):
        raise TimingError('TIMING_MISSING','镜头须登记timing及动作拍/动作秒数依据，不能只以台词推断全部时长')
    if t.get('padding_only'):raise TimingError('EMPTY_PADDING','禁止为凑30秒增加无意义空镜或重复反应')
    text=''.join((f.get('spoken_text') if f.get('spoken_text') is not None else f.get('text')) or '' for f in shot.get('speech',[]))
    chars=spoken_count(text);speech=chars/settings['speech_chars_per_second']
    if 'action_seconds' in t:
        action=number(t['action_seconds'],'action_seconds');beats=None
    else:
        beats=number(t['action_beats'],'action_beats')
        if not beats.is_integer():raise TimingError('TIMING_VALUE','action_beats须为非负整数')
        if beats>0 and settings.get('action_seconds_per_beat') is None:raise TimingError('TIMING_BASIS','公司口径需登记实际动作秒数或明确的动作拍耗时，不自动逐拍加2.2秒')
        action=beats*settings.get('action_seconds_per_beat',0)
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
        'non_speech_seconds_estimate':action+pause+transition-overlap,
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

def episode_dialogue_budgets(project,settings):
    """Initial per-episode estimate from approved speech units, independent of shot count."""
    buckets={};seen=set();issues=[]
    for item in project.get('episodes',[]):
        try:buckets[int(item['id'])]=0
        except (KeyError,TypeError,ValueError):issues.append({'code':'DIALOGUE_EPISODE','object':str(item.get('id')),'message':'集数登记无效'})
    for d in project.get('dialogues',[]):
        did=d.get('id');ep=d.get('episode')
        if not did or did in seen:
            issues.append({'code':'DIALOGUE_ID','object':str(did),'message':'计数前须锁定不重复的台词ID'});continue
        seen.add(did)
        if ep is None:
            issues.append({'code':'DIALOGUE_EPISODE','object':str(did),'message':'台词计数前须锁定集数'});continue
        try:ep=int(ep)
        except (TypeError,ValueError):
            issues.append({'code':'DIALOGUE_EPISODE','object':str(did),'message':'台词集数无效'});continue
        text=(d.get('spoken_text') if d.get('spoken_text') is not None else d.get('text')) or ''
        buckets[ep]=buckets.get(ep,0)+spoken_count(text)
    rows=[]
    if settings.get('timing_mode')!='company-speech-ratio':return rows,issues
    for ep,chars in sorted(buckets.items()):
        voice=chars/settings['speech_chars_per_second']
        if voice<=EPS:
            rows.append({'episode':ep,'spoken_characters':chars,'speech_seconds_estimate':voice,
                'ratio_status':'待复核：无对白，按实际动作估时','minimum_groups_by_budget':None});continue
        lo=voice*settings['non_speech_ratio_min'];hi=voice*settings['non_speech_ratio_max'];target=voice*settings['non_speech_ratio_target']
        rows.append({'episode':ep,'spoken_characters':chars,'speech_seconds_estimate':voice,
          'non_speech_budget_min':lo,'non_speech_budget_max':hi,'non_speech_target':target,
          'story_seconds_min':voice+lo,'story_seconds_max':voice+hi,'story_seconds_target':voice+target,
          'minimum_groups_by_budget':minimum_groups(voice+target),
          'budget_generated_seconds':REQUEST_SECONDS*minimum_groups(voice+target),
          'budget_headroom_seconds':REQUEST_SECONDS*minimum_groups(voice+target)-(voice+target),
          'ratio_status':'预算估算；逐镜动作、切点与尾部裁剪待核'})
    return rows,issues

def build_plan(project,groups=None):
    groups=project.get('groups',[]) if groups is None else groups;issues=[];warnings=[];rows=[];episode_rows={}
    def fail(code,obj,msg):issues.append({'code':code,'object':obj,'message':msg})
    def warn(code,obj,msg):warnings.append({'code':code,'object':obj,'message':msg})
    try:settings=timing_settings(project)
    except TimingError as e:return {'basis':'估算，非试读或成片实测','episodes':[],'groups':[],'issues':[{'code':e.code,'object':'settings','message':str(e)}]}
    company=settings.get('timing_mode')=='company-speech-ratio'
    budgets,budget_issues=episode_dialogue_budgets(project,settings);issues.extend(budget_issues)
    for g in groups:
        gid=g['id'];entries=[];estimated=0;complete=True;issue_start=len(issues);shots=g.get('shots',[])
        try:
            if number(g.get('expected_seconds'),'expected_seconds',True)!=REQUEST_SECONDS:fail('REQUEST_SECONDS',gid,'生成组和真实请求必须为30秒')
            allocated=sum(number(s.get('seconds'),'shot seconds',True) for s in shots)
            if abs(allocated-REQUEST_SECONDS)>EPS:fail('DURATION',gid,'逐镜分配秒数合计必须精确30秒')
        except TimingError as e:allocated=0;fail(e.code,gid,str(e))
        for s in shots:
            try:
                metrics=estimate_shot(s,settings);sec=number(s.get('seconds'),'shot seconds',True);need=metrics['estimated_seconds'];estimated+=need
                if need>sec+EPS:fail('TIMING_CAPACITY',s['id'],'对白、动作、停顿和转场估算超出本镜分配；调整镜界或增加组')
                if sec>need+EPS and not s['timing'].get('allocation_basis'):fail('TIMING_HEADROOM',s['id'],'余量须说明实际用途或可裁剪尾部，不能自动填无意义空镜')
                if company and sec>=10 and s.get('speech'):
                    warn('SHOT_PACING_REVIEW',s['id'],'长对白镜需依据语义复核说话者、听者反应及相关道具；有叙事需要可保留，不机械限长')
                entries.append({'shot_id':s['id'],'allocated_seconds':sec,**metrics})
            except TimingError as e:complete=False;fail(e.code,s['id'],str(e))
        if estimated>REQUEST_SECONDS+EPS:fail('GROUP_CAPACITY',gid,'整组剧情估算超过30秒，需调整或增加组')
        tail=0
        if company and 'trim_tail_seconds' in g:
            try:
                tail=number(g['trim_tail_seconds'],'trim_tail_seconds')
                if tail>0 and not g.get('trim_tail_basis'):fail('TRIM_BASIS',gid,'可剪尾部必须说明具体位置和依据')
                if tail>REQUEST_SECONDS-estimated+EPS or not shots or tail>number(shots[-1].get('seconds'),'last shot',True)+EPS:
                    fail('TRIM_CAPACITY',gid,'标记的可剪尾部超过估算余量或末镜时长')
            except TimingError as e:fail(e.code,gid,str(e))
        row={'id':gid,'episode':g['episode'],'estimated_seconds':estimated,'complete_estimate':complete,'allocated_seconds':allocated,
             'headroom_seconds_estimate':REQUEST_SECONDS-estimated,'generated_seconds':REQUEST_SECONDS,
             'trim_tail_seconds':tail if 'trim_tail_seconds' in g else None,
             'trim_tail_basis':g.get('trim_tail_basis'),
             'effective_story_seconds':REQUEST_SECONDS-tail if 'trim_tail_seconds' in g else None,
             'shots':entries,'status':'不通过' if len(issues)>issue_start else '通过'};rows.append(row)
        uses=g.get('uses') or [{'episode':g['episode'],'shot_ids':[s['id'] for s in shots]}]
        for use in uses:
            ep=int(use['episode']);r=episode_rows.setdefault(ep,{'episode':ep,'group_ids':[],'estimated_seconds':0,'allocated_usage_seconds':0,'complete_estimate':True,'group_checks_passed':True,'spoken_characters':0,'speech_seconds_estimate':0,'non_speech_actual_seconds_estimate':0,'trim_tail_seconds':0,'trim_tail_declared_groups':[],'trim_tail_unknown_groups':[]})
            r['group_ids'].append(gid);ids=set(use.get('shot_ids',[]));selected=[x for x in entries if x['shot_id'] in ids]
            r['estimated_seconds']+=sum(x['estimated_seconds'] for x in selected)
            r['spoken_characters']+=sum(x['spoken_characters'] for x in selected)
            r['speech_seconds_estimate']+=sum(x['speech_seconds_estimate'] for x in selected)
            r['non_speech_actual_seconds_estimate']+=sum(x['non_speech_seconds_estimate'] for x in selected)
            if shots and shots[-1]['id'] in ids:
                if 'trim_tail_seconds' in g:r['trim_tail_seconds']+=tail;r['trim_tail_declared_groups'].append(gid)
                else:r['trim_tail_unknown_groups'].append(gid)
            r['allocated_usage_seconds']+=sum(x['allocated_seconds'] for x in selected);r['complete_estimate']&=complete;r['group_checks_passed']&=row['status']=='通过'
    for ep,r in sorted(episode_rows.items()):
        r['group_ids']=list(dict.fromkeys(r['group_ids']));r['planned_groups']=len(r['group_ids']);r['minimum_groups_by_estimate']=minimum_groups(r['estimated_seconds'])
        r['generated_seconds']=REQUEST_SECONDS*r['planned_groups'];r['headroom_seconds_estimate']=r['allocated_usage_seconds']-r['estimated_seconds']
        r['status']='不通过' if not r['complete_estimate'] or not r['group_checks_passed'] or r['headroom_seconds_estimate']<-EPS else '通过'
        if company:
            voice=r['speech_seconds_estimate'];actual=r['non_speech_actual_seconds_estimate']
            if voice<=EPS:
                r.update(non_speech_budget_min=None,non_speech_budget_max=None,non_speech_target=None,ratio_status='待复核：无对白，按实际动作估时',planning_estimate_seconds=r['estimated_seconds'])
                warn('ZERO_SPEECH',str(ep),'无对白不等于无剧情；按实际动作估时，百分比不适用')
            else:
                low=voice*settings['non_speech_ratio_min'];high=voice*settings['non_speech_ratio_max'];target=voice*settings['non_speech_ratio_target']
                r.update(non_speech_budget_min=low,non_speech_budget_max=high,non_speech_target=target,
                         planning_estimate_seconds=max(r['estimated_seconds'],voice+target),
                         ratio_status='目标内' if low-EPS<=actual<=high+EPS else '待复核：按实际动作说明差异')
                if actual<low-EPS:warn('NON_SPEECH_SHORTFALL',str(ep),'已登记无对白内容低于10%目标；复核有意义的动作与反应，勿机械补空镜')
                if actual>high+EPS:warn('NON_SPEECH_OVERAGE',str(ep),'实际必要的无对白内容超过15%目标；记录原因并复核，不能删减动作或假设重叠')
            r['minimum_groups_by_estimate']=minimum_groups(r['planning_estimate_seconds'])
            r['unallocated_usage_seconds']=max(0.0,r['headroom_seconds_estimate']-r['trim_tail_seconds'])
            if not r['trim_tail_declared_groups']:r['trim_tail_seconds']=None
            if r['unallocated_usage_seconds']>EPS:
                warn('UNACCOUNTED_HEADROOM',str(ep),'已排镜头时长含尚未登记用途的余量；标明真实反应/动作或有证据的可剪尾部，不能直接认作有效剧情')
            if r['status']=='通过' and (r['ratio_status']!='目标内' or r['unallocated_usage_seconds']>EPS):r['status']='待复核'
    basis=('按对白和OS每秒字数、整集无对白10%—15%预算及有依据的动作/重叠估算，非试读或成片实测；生成余量不等于可剪尾部' if company else '按去标点可发声字数、动作拍/具体秒数、停顿、转场及有证据的重叠关系估算；非试读或成片实测')
    return {'basis':basis,'settings':settings,'episodes':list(episode_rows.values()),'episode_dialogue_budgets':budgets,
            'groups':rows,'issues':issues,'warnings':warnings,'total_requests':len(groups),'generated_seconds':REQUEST_SECONDS*len(groups)}

def assert_valid_plan(project):
    report=build_plan(project)
    if report['issues']:raise ValueError('Timing plan invalid: '+str(report['issues'][0]))
    return report

def render_summary(report):
    esc=lambda x:html.escape(str(x))
    fmt=lambda v:round(v,2) if isinstance(v,(int,float)) and not isinstance(v,bool) else ('待核' if v is None else v)
    out=['<h2>逐集30秒分组与时长估算</h2><p>'+esc(report['basis'])+'。组数下限需按剧情切点核对；估算余量不等于可剪尾部。</p>']
    if report.get('settings',{}).get('timing_mode')=='company-speech-ratio':
        out.append('<h3>分镜前：全量对白和OS预算</h3><table border="1" cellpadding="6"><tr><th>集</th><th>可发声字数</th><th>台词/OS秒</th><th>无对白预算10%—15%秒</th><th>剧情估计区间秒</th><th>按目标算术最低组</th><th>按目标请求秒</th><th>估算余量秒</th></tr>')
        for x in report.get('episode_dialogue_budgets',[]):
            budget=(str(fmt(x.get('non_speech_budget_min')))+'—'+str(fmt(x.get('non_speech_budget_max'))))
            story=(str(fmt(x.get('story_seconds_min')))+'—'+str(fmt(x.get('story_seconds_max'))))
            out.append('<tr>'+''.join('<td>'+esc(fmt(v))+'</td>' for v in (x['episode'],x['spoken_characters'],x['speech_seconds_estimate'],budget,story,x.get('minimum_groups_by_budget'),x.get('budget_generated_seconds'),x.get('budget_headroom_seconds')))+'</tr>')
        out.append('</table><p>纯动作无对白集的比例不适用，按有依据的实际动作估时；纯对白按实际片段回拼，数字等用已核对读法。</p>')
        out.append('<h3>已排镜头：实际估算与复核</h3><table border="1" cellpadding="6"><tr><th>集</th><th>片段可发声字数</th><th>台词/OS秒</th><th>无对白预算10%—15%秒</th><th>已登记无对白秒</th><th>剧情计划秒</th><th>最低组/已排组</th><th>请求秒</th><th>已声明可剪尾秒</th><th>尾部未声明组</th><th>用途待核秒</th><th>比例状态</th></tr>')
        for x in report.get('episodes',[]):
            budget=str(fmt(x.get('non_speech_budget_min')))+'—'+str(fmt(x.get('non_speech_budget_max')))
            count=str(x['minimum_groups_by_estimate'])+'/'+str(x['planned_groups'])
            vals=(x['episode'],x['spoken_characters'],x['speech_seconds_estimate'],budget,x['non_speech_actual_seconds_estimate'],x['planning_estimate_seconds'],count,x['generated_seconds'],x['trim_tail_seconds'],len(x['trim_tail_unknown_groups']),x['unallocated_usage_seconds'],x['ratio_status'])
            out.append('<tr>'+''.join('<td>'+esc(fmt(v))+'</td>' for v in vals)+'</tr>')
    else:
        out.append('<table border="1" cellpadding="6"><tr><th>集</th><th>剧情估算秒</th><th>算术最低组数</th><th>已排组数</th><th>生成秒</th><th>本集使用秒</th><th>估算余量秒</th></tr>')
        for x in report.get('episodes',[]):
            vals=(x['episode'],x['estimated_seconds'],x['minimum_groups_by_estimate'],x['planned_groups'],x['generated_seconds'],x['allocated_usage_seconds'],x['headroom_seconds_estimate'])
            out.append('<tr>'+''.join('<td>'+esc(fmt(v))+'</td>' for v in vals)+'</tr>')
    out.append('</table><p>无对白时长按情节和真实重叠核对，不机械加空镜；可剪尾部只统计明确标记，不自动将估算余量全部视为可剪。实际语速和成片效果仍需复核。</p><details><summary>逐镜依据、余量及检查问题</summary><pre>'+esc(json.dumps(report,ensure_ascii=False,indent=2))+'</pre></details>')
    return ''.join(out)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='本地估算逐集30秒分组，不生成媒体');ap.add_argument('project');ap.add_argument('--out');ap.add_argument('--pack-atoms',action='store_true');a=ap.parse_args();p=load(a.project)
    report=build_plan(p)
    if a.pack_atoms:
        s=timing_settings(p);report['suggested_partitions']=[{'episode':ep['id'],'groups':plan_atoms([shot for g in p.get('groups',[]) if g['episode']==ep['id'] for shot in g.get('shots',[])],s)} for ep in p.get('episodes',[])]
    if a.out:save(a.out,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(report['issues']))
