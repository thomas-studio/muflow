"""Durable, full-source local stage queue. No network, paid execution or video approval."""
import argparse,copy,datetime,json,shutil
from pathlib import Path
from core import load,save,resolve,sha,digest,safe_name
from source_coverage import coverage

QUEUE='stage_queue.json'
STAGES=('screenplay','storyboard_assets','images')
BATCH_SIZES={'screenplay':5,'storyboard_assets':5,'images':20}
STATUSES=('pending','running','completed','failed','blocked')

def _now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def _read(project_path):
    pp=Path(project_path);return pp,load(pp),pp.parent

def _basis(p,base):
    c=coverage(p,base)
    if c.get('basis_status')!='verified' or not c.get('source_episodes') or not c.get('source_sha256'):
        raise ValueError('Stage queue needs an independently verified full source episode baseline; project registration or a five-episode production_scope is insufficient')
    return {'source_sha256':c['source_sha256'],'episode_ids':c['source_episodes'],'source_kind':c['source_kind'],'evidence':c['evidence']}

def _event(q,action,detail):q.setdefault('events',[]).append({'action':action,'detail':detail,'at':_now()})
def _write(base,q):q['updated_at']=_now();save(Path(base)/QUEUE,q)
def _stage(q,name):return q['stages'][name]
def _episode_batches(episodes,stage):
    prefix='SC' if stage=='screenplay' else 'SA'
    return [{'id':prefix+str(i//5+1).zfill(3),'stage':stage,'episodes':episodes[i:i+5],'status':'pending','artifacts':[],'history':[]} for i in range(0,len(episodes),5)]

def _asset_specs(p):
    rows={}
    for a in p.get('assets',[]):
        aid=a['id']
        if aid in rows:raise ValueError('Duplicate stage queue asset '+aid)
        rows[aid]={'asset_id':aid,'parent_id':a.get('parent_id'),'mode':a.get('mode'),'status':'pending','artifacts':[]}
    for aid in rows:
        chain=[];cur=aid
        while cur:
            if cur in chain:raise ValueError('Image queue parent cycle '+aid)
            if cur not in rows:raise ValueError('Unknown image queue parent '+cur)
            chain.append(cur);cur=rows[cur]['parent_id']
    return rows

def initialize(project_path):
    """Create/update the full plan. Existing source coverage and task history never shrink."""
    pp,p,base=_read(project_path);basis=_basis(p,base);path=base/QUEUE;specs=_asset_specs(p)
    if path.exists():
        q=load(path)
        if q.get('project_id')!=p.get('project_id'):raise ValueError('Stage queue belongs to another project')
        if q.get('source_basis',{}).get('source_sha256')!=basis['source_sha256'] or q.get('source_basis',{}).get('episode_ids')!=basis['episode_ids']:
            raise ValueError('Full source baseline changed; preserve this queue and explicitly rebaseline a new plan, never silently shrink/reset completed work')
        before=copy.deepcopy(q);old=_stage(q,'images')['items']
        if set(old)-set(specs):raise ValueError('Asset plan would shrink; preserve existing image tasks and reconcile explicitly')
        for aid,spec in specs.items():
            if aid not in old:old[aid]=spec;_event(q,'asset_added',aid)
            elif any(old[aid].get(k)!=spec.get(k) for k in ('parent_id','mode')):raise ValueError('Existing image task lineage/operation changed; register a new asset version ID: '+aid)
        if q!=before:_write(base,q)
        return q
    episodes=basis['episode_ids'];q={'schema_version':1,'project_id':p['project_id'],'source_basis':basis,'full_episode_ids':episodes,'batch_sizes':dict(BATCH_SIZES),
        'stages':{stage:{'batches':_episode_batches(episodes,stage)} for stage in STAGES[:2]},'cursor':None,'events':[],'created_at':_now(),
        'generation_authorized':False,'video_authorized':False,'meaning':'本地任务台账仅记录就绪、产物与断点；不替代生图授权/审核，也不授权视频'}
    q['stages']['images']={'batches':[],'items':specs};_event(q,'initialized',{'full_episodes':episodes,'batch_sizes':BATCH_SIZES});_write(base,q);return q

# Explicit alias for callers refreshing after all screenplay/storyboard batches register assets.
update=initialize

def _artifact(base,row):
    if not isinstance(row,dict) or not row.get('path') or not row.get('sha256'):raise ValueError('Concrete artifact path and sha256 required')
    p=resolve(base,row['path'])
    if not p.is_file() or p.stat().st_size==0:raise ValueError('Completed batch artifact must be a nonempty actual file: '+str(row.get('path')))
    if sha(p)!=row['sha256']:raise ValueError('Completed batch artifact hash changed: '+str(row['path']))
    return row

def _invalid_completed(q,base,p=None):
    errors=[]
    for stage in STAGES:
        for batch in _stage(q,stage)['batches']:
            if batch['status']!='completed':continue
            try:
                if not batch.get('artifacts') or not batch.get('completion',{}).get('evidence'):raise ValueError('Missing concrete completion evidence')
                for row in batch['artifacts']:_artifact(base,row)
            except ValueError as e:errors.append((stage,batch,str(e)))
    for aid,item in _stage(q,'images')['items'].items():
        if item['status']!='completed':continue
        try:
            if not item.get('artifacts'):raise ValueError('Missing image completion artifact')
            for row in item['artifacts']:_artifact(base,row)
            if p is not None:
                a=next((a for a in p.get('assets',[]) if a['id']==aid),None)
                if not a or a.get('sha256')!=item['artifacts'][0]['sha256']:raise ValueError('Completed image is no longer the currently registered candidate: '+aid)
                from review_state import asset_subject
                if item.get('completion',{}).get('subject_digest') and item['completion']['subject_digest']!=asset_subject(p,a):raise ValueError('Completed image content/identity lineage changed: '+aid)
        except ValueError as e:errors.append(('images',item,str(e)))
    return errors

def _invalidate(q,base,p):
    errors=_invalid_completed(q,base,p)
    for stage,obj,error in errors:
        obj['status']='blocked';obj['error']=error;obj.setdefault('history',[]).append({'status':'blocked','evidence':error,'at':_now()})
        _event(q,'artifact_invalidated',{'stage':stage,'id':obj.get('id') or obj.get('asset_id'),'error':error})
        if stage=='images' and obj.get('asset_id'):
            for batch in _stage(q,'images')['batches']:
                if obj['asset_id'] in batch['asset_ids']:
                    batch['status']='blocked';batch['error']=error
                    batch.setdefault('history',[]).append({'status':'blocked','evidence':error,'at':_now()})
    return errors

def _qualified(p,base,a):
    if not a.get('local_path') or not a.get('sha256'):return None
    try:
        row={'path':a['local_path'],'sha256':a['sha256'],'asset_id':a['id'],'kind':'image'};_artifact(base,row)
        from PIL import Image
        with Image.open(resolve(base,a['local_path'])) as im:im.verify()
        from review_state import asset_ready
        asset_ready(p,a)
        if a.get('parent_id'):
            parent=next(x for x in p.get('assets',[]) if x['id']==a['parent_id']);audit=a.get('edit_audit',{})
            if audit.get('operation')!='image_edit' or audit.get('input_asset_id')!=parent['id'] or audit.get('input_sha256')!=parent.get('sha256') or audit.get('input_count')!=1 or sha(resolve(base,parent['local_path']))!=parent.get('sha256'):return None
        return row
    except (OSError,ValueError,KeyError):return None

def _provided_identity(p,base,a):
    """A supplied locked face may be a mother before final whole-project adoption."""
    if a.get('mode')!='reuse' or not a.get('local_path') or not a.get('sha256'):return None
    for source in p.get('input_assets',[]):
        if source.get('asset_id')!=a['id'] or not source.get('identity_locked') or source.get('sha256')!=a.get('sha256'):continue
        try:
            row={'path':a['local_path'],'sha256':a['sha256'],'asset_id':a['id'],'kind':'image','identity_locked_input':True,'input_metadata':copy.deepcopy(source)}
            _artifact(base,row)
            if not source.get('local_path') or sha(resolve(base,source['local_path']))!=a['sha256']:return None
            from PIL import Image
            with Image.open(resolve(base,a['local_path'])) as im:im.verify()
            return row
        except (ValueError,OSError,KeyError):return None
    return None

def _reuse(q,p,base):
    items=_stage(q,'images')['items'];changed=False
    for a in p.get('assets',[]):
        item=items[a['id']]
        if item['status']!='pending':continue
        row=_qualified(p,base,a) or _provided_identity(p,base,a)
        if row:
            from review_state import asset_subject
            item.update(status='completed',artifacts=[row],completion={'subject_digest':asset_subject(p,a),'evidence':'真实给定锁脸母图已核对输入元数据与原图哈希；可派生，不代表最终验收' if row.get('identity_locked_input') else '复用实际文件：哈希与当前审核模式要求均已核对，不重新生图','kind':'provided_identity_reuse' if row.get('identity_locked_input') else 'qualified_reuse','at':_now()});changed=True
            item.setdefault('completion_history',[]).append({'completion':copy.deepcopy(item['completion']),'artifacts':copy.deepcopy(item['artifacts'])})
            _event(q,'provided_identity_reuse' if row.get('identity_locked_input') else 'qualified_reuse',{'asset_id':a['id'],'sha256':a['sha256']})
    for batch in _stage(q,'images')['batches']:
        if batch['status']=='pending' and all(items[aid]['status']=='completed' for aid in batch['asset_ids']):
            batch.update(status='completed',artifacts=[r for aid in batch['asset_ids'] for r in items[aid]['artifacts']],completion={'evidence':'本批已有实际复用产物，无需重新生图；生成完成不代表最终验收','kind':'local_reuse','at':_now()});changed=True
    return changed

def _parents_ready(q,p,base,aid):
    items=_stage(q,'images')['items'];parent=items[aid].get('parent_id')
    if items[aid].get('mode')=='reuse' and items[aid]['status']!='completed':return False,'该资产指定复用，先核对已有图的当前审核证据，不重新生图：'+aid
    if not parent:return True,None
    if items[parent]['status']!='completed':return False,'母图尚未完成：'+parent
    a=next(a for a in p['assets'] if a['id']==parent)
    try:
        from review_state import mother_ready
        mother_ready(p,base,a);return True,None
    except (ValueError,OSError,KeyError) as e:return False,'母图尚未具备实际参考编辑条件：'+parent+' · '+str(e)

def _find(q,batch_id):
    for stage in STAGES:
        for b in _stage(q,stage)['batches']:
            if b['id']==batch_id:return stage,b
    raise ValueError('Unknown stage batch '+batch_id)

def _earlier_complete(q,stage):
    for earlier in STAGES[:STAGES.index(stage)]:
        batches=_stage(q,earlier)['batches']
        if not batches or any(b['status']!='completed' for b in batches):return False
    return True

def _finish_state(q,p,base):
    from review_state import readiness
    try:current=readiness(p,base)
    except (ValueError,OSError,KeyError) as e:current={'label':'待核实','source_full_complete':False,'reviews_complete':False,'evidence':[{'object':'readiness','reason':str(e)}]}
    ready=current.get('label')=='满血'
    return {'action':'ready_preview' if ready else 'awaiting_review','stage':'prevideo_preview' if ready else 'asset_review','next_action':'build_prevideo_preview' if ready else 'complete_ai_review_summary_and_user_feedback',
        'readiness':current,'full_structure_complete':current.get('source_full_complete',False),
        'reason':'全部图片当前实际审核与全剧结构均已就绪；只推进跑前预览，视频仍未授权' if ready else '图片产出不等于验收通过；仍需全剧AI审图汇总、用户意见处理与问题图复核，结构缺项也须补齐'}

def next_batch(project_path):
    """Return the next ready batch and persist its cursor. Does not execute any task."""
    initialize(project_path);pp,p,base=_read(project_path);q=load(base/QUEUE);before=copy.deepcopy(q)
    errors=_invalidate(q,base,p)
    if errors:
        result={'action':'blocked','stage':errors[0][0],'batch_id':errors[0][1].get('id'),'reason':errors[0][2]}
    else:
        result=None
        for stage in STAGES[:2]:
            pending=[b for b in _stage(q,stage)['batches'] if b['status']!='completed']
            if pending:
                b=pending[0];result={'action':'ready' if b['status']=='pending' else b['status'],'stage':stage,'batch_id':b['id'],'episodes':b['episodes'],'status':b['status'],'reason':b.get('error')};break
        if result is None:
            _reuse(q,p,base);items=_stage(q,'images')['items'];batches=_stage(q,'images')['batches'];active=next((b for b in batches if b['status']!='completed'),None)
            if not items:result={'action':'blocked','stage':'images','reason':'全剧分镜资产完成后仍未登记全量资产清单；不能把空图表当作全剧完成'}
            elif active:
                aids=[aid for aid in active['asset_ids'] if items[aid]['status']!='completed'];checks=[_parents_ready(q,p,base,aid) for aid in aids]
                result={'action':('ready' if all(x[0] for x in checks) else 'blocked') if active['status']=='pending' else active['status'],'stage':'images','batch_id':active['id'],'asset_ids':aids,'status':active['status'],'reason':next((x[1] for x in checks if not x[0]),active.get('error'))}
            else:
                pending=[aid for aid,item in items.items() if item['status']=='pending'];eligible=[aid for aid in pending if _parents_ready(q,p,base,aid)[0]][:20]
                if eligible:
                    active={'id':'IM'+str(len(batches)+1).zfill(3),'stage':'images','asset_ids':eligible,'status':'pending','artifacts':[],'history':[]};batches.append(active);_event(q,'image_batch_prepared',{'batch_id':active['id'],'asset_ids':eligible})
                    result={'action':'ready','stage':'images','batch_id':active['id'],'asset_ids':eligible,'status':'pending'}
                elif all(item['status']=='completed' for item in items.values()):result=_finish_state(q,p,base)
                else:result={'action':'blocked','stage':'images','reason':next((_parents_ready(q,p,base,aid)[1] for aid in pending if not _parents_ready(q,p,base,aid)[0]),'图片任务存在failed/blocked状态，按原批次复核后显式retry')}
    if result.get('stage')=='images' and result.get('asset_ids'):
        result['asset_operations']=[{'asset_id':aid,'operation':_stage(q,'images')['items'][aid].get('mode'),'generation_required':_stage(q,'images')['items'][aid].get('mode')!='reuse'} for aid in result['asset_ids']]
    result.update(generation_authorized=False,video_authorized=False);q['cursor']={k:result.get(k) for k in ('stage','batch_id','action')}
    if q!=before:_write(base,q)
    return result

def start_batch(project_path,batch_id):
    ready=next_batch(project_path)
    if ready.get('batch_id')!=batch_id or ready['action'] not in ('ready','running'):raise ValueError('This batch is not the next ready full-stage task: '+batch_id)
    pp,p,base=_read(project_path);q=load(base/QUEUE);stage,b=_find(q,batch_id)
    if b['status']=='running':return b
    b['status']='running';b['started_at']=_now();b['history'].append({'status':'running','at':b['started_at']});_event(q,'started',batch_id)
    if stage=='images':
        for aid in b['asset_ids']:
            if _stage(q,'images')['items'][aid]['status']!='completed':_stage(q,'images')['items'][aid]['status']='running'
    _write(base,q);return b

def _snapshot_accumulating_tables(base,batch_id,artifacts):
    """Freeze the actual table bytes so later --refresh cannot invalidate an earlier batch."""
    frozen=[]
    for original in artifacts:
        row=copy.deepcopy(original)
        if row.get('kind')=='asset_table':
            src=resolve(base,row['path']);folder=Path(base)/'99_工作区'/'阶段队列证据'/safe_name(batch_id)
            if row.get('evidence_snapshot') and src.resolve().parent==folder.resolve():
                frozen.append(row);continue
            dest=folder/(safe_name(src.stem)+'_'+row['sha256']+src.suffix.lower())
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if sha(dest)!=row['sha256']:raise ValueError('Immutable stage evidence snapshot changed: '+str(dest))
            else:shutil.copyfile(src,dest)
            if sha(dest)!=row['sha256']:raise ValueError('Stage evidence copy hash mismatch')
            row.update(path=str(dest.relative_to(base)),source_artifact={'path':original['path'],'sha256':original['sha256']},evidence_snapshot=True)
        frozen.append(row)
    return frozen

def complete_batch(project_path,batch_id,artifacts,evidence):
    if not evidence or not isinstance(artifacts,list) or not artifacts:raise ValueError('Nonempty concrete artifact list and completion evidence required')
    initialize(project_path);pp,p,base=_read(project_path);q=load(base/QUEUE);stage,b=_find(q,batch_id)
    if not _earlier_complete(q,stage):raise ValueError('Finish every full-source batch in earlier stages before downstream completion')
    if _invalid_completed(q,base,p):raise ValueError('Earlier completion artifacts changed; reconcile original batches first')
    input_fp=digest({'artifacts':artifacts,'evidence':evidence})
    if b['status']=='completed':
        if input_fp in (b.get('completion',{}).get('proof_digest'),b.get('completion',{}).get('input_proof_digest')):return b
        raise ValueError('Completed batch already has different evidence; preserve it and reconcile explicitly')
    if b['status']!='running':raise ValueError('Start the next ready batch before registering completion')
    for row in artifacts:_artifact(base,row)
    if stage in STAGES[:2]:
        kinds=('screenplay',) if stage=='screenplay' else ('storyboard','asset_table')
        for kind in kinds:
            covered={int(ep) for row in artifacts if row.get('kind')==kind for ep in row.get('episodes',[])}
            if not set(b['episodes'])<=covered:raise ValueError('Missing '+kind+' artifact evidence for every episode in batch '+batch_id)
    else:
        assets={a['id']:a for a in p.get('assets',[])};items=_stage(q,'images')['items'];rows={r.get('asset_id'):r for r in artifacts}
        for aid in b['asset_ids']:
            if items[aid]['status']=='completed':continue
            row=rows.get(aid);a=assets[aid]
            if not row or row.get('kind')!='image' or row['sha256']!=a.get('sha256') or sha(resolve(base,a.get('local_path','')))!=row['sha256']:raise ValueError('Image completion must match each actually registered local asset: '+aid)
            from PIL import Image
            with Image.open(resolve(base,row['path'])) as im:im.verify()
            ok,why=_parents_ready(q,p,base,aid)
            if not ok:raise ValueError(why)
            if a.get('parent_id'):
                parent=assets[a['parent_id']];audit=a.get('edit_audit',{})
                if audit.get('operation')!='image_edit' or audit.get('input_asset_id')!=parent['id'] or audit.get('input_sha256')!=parent.get('sha256') or audit.get('input_count')!=1:raise ValueError('Actual mother reference audit missing for completed derivative '+aid)
        for aid in b['asset_ids']:
            if items[aid]['status']!='completed':items[aid].update(status='completed',artifacts=[rows[aid]],completion={'subject_digest':__import__('review_state').asset_subject(p,assets[aid]),'evidence':evidence,'kind':'registered_output','at':_now()})
    artifacts=_snapshot_accumulating_tables(base,batch_id,artifacts)
    fp=digest({'artifacts':artifacts,'evidence':evidence})
    b.update(status='completed',artifacts=copy.deepcopy(artifacts),completion={'evidence':evidence,'proof_digest':fp,'input_proof_digest':input_fp,'at':_now()})
    b.setdefault('completion_history',[]).append({'completion':copy.deepcopy(b['completion']),'artifacts':copy.deepcopy(b['artifacts'])})
    if stage=='images':
        for aid in b['asset_ids']:
            item=_stage(q,'images')['items'][aid];item.setdefault('completion_history',[]).append({'completion':copy.deepcopy(item.get('completion')),'artifacts':copy.deepcopy(item['artifacts'])})
    b['history'].append({'status':'completed','evidence':evidence,'at':_now()});_event(q,'completed',batch_id);_write(base,q);return b

def set_status(project_path,batch_id,status,evidence):
    if status not in ('failed','blocked') or not evidence:raise ValueError('Explicit failure/block status and evidence required')
    pp,p,base=_read(project_path);q=load(base/QUEUE);stage,b=_find(q,batch_id)
    if b['status']=='completed':raise ValueError('Preserve completed batch evidence; changed artifacts are invalidated through next')
    b.update(status=status,error=evidence);b['history'].append({'status':status,'evidence':evidence,'at':_now()});_event(q,status,batch_id)
    if stage=='images':
        for aid in b['asset_ids']:
            if _stage(q,'images')['items'][aid]['status']!='completed':_stage(q,'images')['items'][aid]['status']=status
    _write(base,q);return b

def retry_batch(project_path,batch_id,evidence):
    if not evidence:raise ValueError('Explicit reconciliation/retry evidence required')
    pp,p,base=_read(project_path);q=load(base/QUEUE);stage,b=_find(q,batch_id)
    if b['status'] not in ('failed','blocked'):raise ValueError('Only failed/blocked local batches can retry')
    b.update(status='pending',error=None);b['history'].append({'status':'pending','evidence':evidence,'at':_now()});_event(q,'retry',batch_id)
    if stage=='images':
        for aid in b['asset_ids']:
            if _stage(q,'images')['items'][aid]['status'] in ('failed','blocked'):_stage(q,'images')['items'][aid]['status']='pending'
    _write(base,q);return b

def progress(p,base=None):
    """Read-only UI snapshot; accepts a project path, or project data plus its base."""
    if isinstance(p,(str,Path)):
        pp=Path(p);base=pp.parent;p=load(pp)
    elif base is None:
        hint=p.get('_project_path') or p.get('project_path')
        if hint:base=Path(hint).parent
        else:base=p.get('base_dir')
    empty={'state':'not_initialized','stage':None,'total':0,'completed':0,'running':0,'failed':0,'blocked':0,'next_batch':None,'cursor':None,'source_episodes':[],'batch_sizes':dict(BATCH_SIZES),'messages':['尚未初始化全剧阶段队列'],'generation_authorized':False,'video_authorized':False}
    if base is None or not (Path(base)/QUEUE).is_file():return empty
    try:q=load(Path(base)/QUEUE);basis=_basis(p,base)
    except (ValueError,OSError,KeyError) as e:return empty|{'state':'blocked','blocked':1,'messages':[str(e)]}
    stale=q.get('project_id')!=p.get('project_id') or q.get('source_basis',{}).get('source_sha256')!=basis['source_sha256'] or q.get('full_episode_ids')!=basis['episode_ids']
    batches=[b for stage in STAGES for b in _stage(q,stage)['batches']];counts={status:sum(b['status']==status for b in batches) for status in STATUSES};errors=_invalid_completed(q,base,p)
    stage=next((s for s in STAGES if (any(b['status']!='completed' for b in _stage(q,s)['batches']) if s!='images' else not _stage(q,s)['items'] or any(i['status']!='completed' for i in _stage(q,s)['items'].values()))),'prevideo_preview')
    final=_finish_state(q,p,base) if stage=='prevideo_preview' else None
    if final:stage=final['stage']
    active=next((b for b in _stage(q,stage)['batches'] if b['status']!='completed'),None) if stage in STAGES else None
    messages=['阶段顺序：全剧剧本每5集 → 全剧分镜与资产表每5集 → 全量资产图每20张；ready仅本地就绪，视频未授权']
    if stale:messages.append('独立全剧范围/原文已变化，旧队列不可继续')
    if errors:messages.extend(e[2] for e in errors)
    if final:messages.append(final['reason'])
    return {'state':'blocked' if stale or errors or counts['failed'] or counts['blocked'] else final['action'] if final else 'active','stage':stage,'total':len(batches),'completed':counts['completed'],'running':counts['running'],'failed':counts['failed'],'blocked':counts['blocked']+int(stale or bool(errors)),'next_batch':active,'cursor':q.get('cursor'),'source_episodes':q['full_episode_ids'],'batch_sizes':q['batch_sizes'],'messages':messages,'updated_at':q.get('updated_at'),'image_items':{s:sum(i['status']==s for i in _stage(q,'images')['items'].values()) for s in STATUSES},'review':final,'generation_authorized':False,'video_authorized':False}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');sp=ap.add_subparsers(dest='command',required=True)
    for name in ('init','next','progress'):sp.add_parser(name)
    for name in ('start','complete','fail','block','retry'):
        p=sp.add_parser(name);p.add_argument('batch_id')
        if name!='start':p.add_argument('--evidence',required=True)
        if name=='complete':p.add_argument('--artifacts',required=True,help='JSON array of actual path/sha256/kind/episodes or asset_id records')
    a=ap.parse_args()
    if a.command=='init':r=initialize(a.project)
    elif a.command=='next':r=next_batch(a.project)
    elif a.command=='progress':r=progress(a.project)
    elif a.command=='start':r=start_batch(a.project,a.batch_id)
    elif a.command=='complete':r=complete_batch(a.project,a.batch_id,load(a.artifacts),a.evidence)
    elif a.command=='retry':r=retry_batch(a.project,a.batch_id,a.evidence)
    else:r=set_status(a.project,a.batch_id,'failed' if a.command=='fail' else 'blocked',a.evidence)
    print(json.dumps(r,ensure_ascii=False,indent=2))
