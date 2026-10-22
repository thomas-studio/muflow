"""Visible exact preview plus scoped authorization. Delivery is not user approval."""
import argparse,datetime,json
from pathlib import Path
from core import load,save,digest,select,authorization,sha,video_contract_check
from validate_project import validate
from build_preview import compile_jobs

def snapshot(directory):
    p=Path(directory)
    return digest({'compiled':load(p/'compiled_jobs.json'),'quote':load(p/'quote_snapshot.json'),'run_scope':load(p/'run_scope.json')})

def record_delivery(directory,evidence):
    if not evidence or not str(evidence).strip():raise ValueError('Actual preview delivery evidence required')
    p=Path(directory);html=p/'全剧跑前预览.html'
    if not html.is_file():raise ValueError('No actual preview panel')
    import shutil
    evidence_dir=p/'delivered_snapshots';evidence_dir.mkdir(exist_ok=True)
    frozen=evidence_dir/(snapshot(p)+'_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.html')
    shutil.copyfile(html,frozen)
    result={'delivered_panel':str(frozen.resolve()),'delivered_panel_sha256':sha(frozen),'snapshot_digest':snapshot(p),'panel_sha256':sha(html),'evidence':evidence,
            'delivered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'meaning':'Agent recorded actual delivery/opening; this is not user approval'}
    save(p/'delivery_receipt.json',result);return result

def prepare(project_path,cfg,preview_dir,episodes=None):
    pp=Path(project_path);data=load(pp);directory=Path(preview_dir);current=compile_jobs(data,pp.parent);frozen=load(directory/'compiled_jobs.json')
    if data.get('schema_version')!=2:raise ValueError('Migrate and review project to schema 2 before paid execution')
    if current!=frozen:raise ValueError('Preview is stale; rebuild with the actual uploaded assets')
    receipt=load(directory/'delivery_receipt.json')
    if receipt.get('snapshot_digest')!=snapshot(directory) or not receipt.get('delivered_panel') or receipt.get('delivered_panel_sha256')!=sha(Path(receipt['delivered_panel'])) or not receipt.get('evidence'):
        raise ValueError('Preview delivery missing or stale')
    authorization(cfg,'video');a=cfg['authorization'];scope=a.get('project_scope',{})
    if scope.get('project_id')!=data['project_id']:raise ValueError('Authorization belongs to a different project')
    if a.get('mode')=='frozen_preview':
        if scope.get('snapshot_digest')!=snapshot(directory):raise ValueError('This exact preview has not been authorized')
    elif a.get('mode')!='project_pre_authorized':raise ValueError('Choose exact preview or explicit project preauthorization')
    groups=select(data,episodes);allowed=scope.get('episodes',[])
    if not {g['id'] for g in groups}<=set(load(directory/'run_scope.json')['group_ids']):raise ValueError('Requested groups outside delivered preview run range')
    used={int(u['episode']) for g in groups for u in (g.get('uses') or [{'episode':g['episode']}])}
    if not allowed or not used<=set(map(int,allowed)):raise ValueError('Requested episodes exceed authorized project range')
    for g in groups:
        if g.get('revision_kind','initial')=='content_redo' and a.get('allow_content_redo') is not True:
            raise ValueError('Paid content redo is not an authorized service-failure retry')
    from workflow_gate import before_video,scope_groups
    before_video(data,pp.parent,snapshot(directory))
    full_scope=scope_groups(data,pp.parent)
    if not {g['id'] for g in full_scope}<=set(load(directory/'run_scope.json')['group_ids']):raise ValueError('Full user-defined review scope is not present in the video preview')
    if not {g['id'] for g in full_scope}<={it['id'] for it in frozen['items']}:raise ValueError('Full preview contains unprepared requests')
    report=validate(data,pp.parent,full_scope)
    if report['issues']:raise ValueError('Readiness failed: '+json.dumps(report['issues'],ensure_ascii=False))
    ids={g['id'] for g in groups};items=[it for it in frozen['items'] if it['id'] in ids];v=cfg.get('video',{})
    if v.get('model')!=current.get('model'):raise ValueError('Preview model differs from paired model')
    price=v.get('max_price_per_attempt_yuan');quote=load(directory/'quote_snapshot.json')
    if price!=quote.get('unit_price_cap_yuan'):raise ValueError('Quote changed; rebuild preview')
    if any(len(it['images'])>v.get('verified_max_images',0) or it['duration'] not in v.get('verified_durations_seconds',[]) for it in items):
        raise ValueError('Request duration/reference count outside verified capabilities; rebuild plan, never truncate references')
    video_contract_check(cfg,items)
    grant={'runtime_config_digest':digest({'video':cfg.get('video'),'contracts':cfg.get('contracts')}),'items_digest':digest(items),'snapshot_digest':snapshot(directory),'delivery_digest':digest(receipt),
           'authorization_digest':digest(a),'evidence':a['evidence'],'project_id':data['project_id']}
    return items,grant

def enforce(items,cfg,grant=None):
    authorization(cfg,'video')
    if not grant or grant.get('runtime_config_digest')!=digest({'video':cfg.get('video'),'contracts':cfg.get('contracts')}) or grant.get('items_digest')!=digest(items) or grant.get('authorization_digest')!=digest(cfg['authorization']) or not grant.get('delivery_digest'):
        raise ValueError('Use the current delivered preview and scoped execution gate')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('preview');p.add_argument('--delivered-evidence',required=True);a=p.parse_args();record_delivery(a.preview,a.delivered_evidence);print('预览交付记录已保存；不代表用户批准。')
