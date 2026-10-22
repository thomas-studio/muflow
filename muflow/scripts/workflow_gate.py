"""Human review boundaries for creative text, generated assets and video execution."""
from pathlib import Path
import datetime
from core import load,save,digest,resolve,sha
from compose_requests import compose_group
from timing_plan import timing_settings,assert_valid_plan,build_plan

SETTING_KEYS=('medium','quality','asset_ratio','scene_ratio','video_ratio','asset_review_mode')
TEXT_ASSET_KEYS=('id','name','kind','mode','parent_id','prompt','purpose','entity_id','state_key','origin')
DELIVERABLES=('screenplay','asset_table','asset_prompts','storyboard','shot_prompts','selfcheck')

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def settings_snapshot(p):
    s=dict(p.get('settings',{}))
    from review_state import mode
    s['asset_review_mode']=mode(p)
    return {k:s.get(k,'16:9' if k in ('asset_ratio','scene_ratio') else None) for k in SETTING_KEYS}|timing_settings(p)

def confirm_project(p,evidence):
    if not evidence:raise ValueError('Actual user settings confirmation evidence required')
    s=settings_snapshot(p)
    if any(not s.get(k) for k in SETTING_KEYS):raise ValueError('Ask the user for missing project settings before creative production')
    p.setdefault('workflow',{})['project_confirmation']={'status':'confirmed','settings_digest':digest(s),'evidence':evidence,'recorded_at':now()}
    return p['workflow']['project_confirmation']

def confirmed(p):
    c=p.get('workflow',{}).get('project_confirmation',{})
    if c.get('status')!='confirmed' or not c.get('evidence') or c.get('settings_digest')!=digest(settings_snapshot(p)):
        raise ValueError('Project settings not confirmed or changed: read inputs, ask missing video ratio/style/scope; fixed30/story-driven timing is not a question')

def scope_groups(p,base=None):
    if base is not None:
        from source_coverage import coverage
        result=coverage(p,base)
        if not result.get('complete'):raise ValueError('Full source/review scope incomplete: '+json_summary(result))
    eps={int(e['id']) for e in p.get('episodes',[])};scope=p.get('workflow',{}).get('production_scope',{})
    chosen=set(map(int,scope.get('episodes',[]))) if scope else eps
    if not chosen or not chosen<=eps:raise ValueError('Production scope missing/invalid')
    if chosen!=eps and not scope.get('user_evidence'):raise ValueError('Only the user may narrow full-series production scope')
    groups=[g for g in p.get('groups',[]) if chosen & {int(u['episode']) for u in (g.get('uses') or [{'episode':g['episode']}])}]
    covered={int(u['episode']) for g in groups for u in (g.get('uses') or [{'episode':g['episode']}])}
    if not chosen<=covered:raise ValueError('Full review scope has episodes without prepared requests')
    return groups

def inputs(p,base):
    out=[];assets={a['id']:a for a in p.get('assets',[])}
    for r in p.get('input_assets',[]):
        a=assets.get(r.get('asset_id'));f=resolve(base,r['local_path']);actual=sha(f)
        if actual!=r.get('sha256') or not a:raise ValueError('Provided identity image changed or not registered')
        if r.get('identity_locked'):
            if a.get('mode')!='reuse' or a.get('sha256')!=actual or sha(resolve(base,a['local_path']))!=actual:
                raise ValueError('Keep the supplied identity original: reuse, do not regenerate/overwrite its face')
        out.append({'asset_id':r['asset_id'],'entity_id':r.get('entity_id'),'sha256':actual,'identity_locked':r.get('identity_locked',False)})
    return out

def lineage(p,base,asset):
    inputs(p,base);assets={a['id']:a for a in p.get('assets',[])}
    for r in p.get('input_assets',[]):
        if not r.get('identity_locked') or asset.get('entity_id')!=r.get('entity_id'):continue
        cur=asset;seen=set()
        while cur and cur.get('id') not in seen:
            seen.add(cur['id'])
            if cur['id']==r['asset_id']:break
            if cur.get('mode')!='edit':raise ValueError('Locked supplied face requires actual image editing, never a fresh text-generated identity')
            cur=assets.get(cur.get('parent_id'))
        else:raise ValueError('Asset lineage does not reach the user-supplied locked identity')

def text_manifest(p,base):
    confirmed(p);provided=inputs(p,base);files={}
    for k in DELIVERABLES:
        value=p.get('deliverables',{}).get(k)
        if not value:raise ValueError('Text-stage delivery missing: '+k)
        f=resolve(base,value);files[k]={'path':str(value),'sha256':sha(f)}
    assets=[{k:a.get(k) for k in TEXT_ASSET_KEYS} for a in p.get('assets',[])]
    if any(a.get('mode') in ('generate','edit') and not a.get('prompt') for a in p.get('assets',[])):raise ValueError('Every generation/edit asset needs an explicit text-stage prompt')
    groups=scope_groups(p,base)
    return {'project_id':p['project_id'],'settings':settings_snapshot(p),'source_sha256':sha(resolve(base,p['source_path'])),
            'timing_plan':build_plan(p,groups),'episodes':p.get('episodes',[]),'scenes':p.get('scenes',[]),'dialogues':p.get('dialogues',[]),'entities':p.get('entities',{}),
            'input_assets':provided,'assets':assets,'requests':[{'id':g['id'],'prompt':compose_group(p,g)} for g in groups],
            'deliverables':files,'production_scope':p.get('workflow',{}).get('production_scope',{})}

def text_ready(p,base):
    assert_valid_plan(p)
    approved=p.get('workflow',{}).get('text_approval',{})
    if approved.get('status')!='approved' or not approved.get('user_evidence') or approved.get('manifest_digest')!=digest(text_manifest(p,base)):
        raise ValueError('Deliver complete text/storyboard/asset prompts and wait for the user to approve image generation')
    from review_evidence import verify_record
    verify_record(approved,'text')

def mark_text_delivery(p,base,snapshot,evidence):
    if not evidence or snapshot.get('manifest_digest')!=digest(text_manifest(p,base)):raise ValueError('Text preview missing/stale or not delivered')
    p.setdefault('workflow',{})['text_delivery']={'manifest_digest':snapshot['manifest_digest'],'evidence':evidence,'recorded_at':now()}

def approve_text(p,base,user_evidence):
    assert_valid_plan(p)
    d=p.get('workflow',{}).get('text_delivery',{});current=digest(text_manifest(p,base))
    if not user_evidence or not d.get('evidence') or d.get('manifest_digest')!=current:raise ValueError('Current complete text preview must be delivered before user approval')
    from review_evidence import check
    event=check(user_evidence,d['recorded_at'],'text')
    p['workflow']['text_approval']={'status':'approved','manifest_digest':current,'user_evidence':event['text'],'user_event':event,'delivered_at':d['recorded_at'],'recorded_at':now()}

def image_generation(p,base,a,cfg):
    confirmed(p);text_ready(p,base);lineage(p,base,a)
    from audit_project_network import require_clean
    require_clean(base)
    if cfg.get('setup',{}).get('status')!='paired':raise ValueError('API pairing incomplete: no builtin/default/alternate generation fallback')
    return settings_snapshot(p)['scene_ratio' if a['kind']=='scene' else 'asset_ratio']

def json_summary(value):
    import json
    return json.dumps(value.get('issues',value),ensure_ascii=False)

def image_manifest(p,base,asset_ids=None):
    from asset_gallery import required_ids
    from review_state import asset_subject
    assets={a['id']:a for a in p.get('assets',[])}
    needed=set(asset_ids) if asset_ids is not None else set(required_ids(p))
    if not needed:raise ValueError('No actual images selected for review')
    out=[]
    for aid in sorted(needed):
        a=assets.get(aid)
        if not a:raise ValueError('Unknown required image '+aid)
        f=resolve(base,a['local_path']);h=sha(f)
        if h!=a.get('sha256'):raise ValueError('Required actual image changed: '+aid)
        if a.get('mode') in ('generate','edit'):
            from PIL import Image
            with Image.open(f) as im:w,hpx=im.size
            ratio=settings_snapshot(p)['scene_ratio' if a['kind']=='scene' else 'asset_ratio'];rw,rh=map(float,ratio.split(':'))
            if abs(w/hpx-rw/rh)>0.015:raise ValueError('Actual generated image aspect ratio differs from confirmed asset ratio: '+aid)
        lineage(p,base,a);out.append({'asset_id':aid,'sha256':h,'subject_digest':asset_subject(p,a)})
    return out

def mark_image_delivery(p,base,gallery,evidence):
    from asset_gallery import verify
    if not evidence:raise ValueError('Actual gallery delivery evidence required')
    snap=verify(p,base,gallery)
    # Delivery can include completed mothers while later images are still pending.
    assets={a['id']:a for a in p.get('assets',[])};shown=[]
    for card in snap.get('manifest',{}).get('cards',[]):
        aid=card.get('asset_id',card.get('id'))
        if aid in assets:
            try:shown.extend(image_manifest(p,base,[aid]))
            except (KeyError,OSError,ValueError,TypeError):pass
    if not shown:
        for aid in assets:
            try:shown.extend(image_manifest(p,base,[aid]))
            except (KeyError,OSError,ValueError):pass
    p.setdefault('workflow',{})['image_delivery']={'gallery_path':str(gallery),'gallery_sha256':snap['panel_sha256'],
       'manifest_digest':snap['manifest_digest'],'items':shown,'evidence':evidence,'recorded_at':now()}

def approve_images(p,base,gallery,delivery_evidence,user_evidence,asset_ids=None):
    text_ready(p,base)
    actual=image_manifest(p,base,asset_ids)
    from asset_gallery import verify
    from review_evidence import check
    # The exact delivered subjects, not display-only HTML bytes, define this adoption.
    snap=verify(p,base,gallery);d=p.get('workflow',{}).get('image_delivery',{})
    delivered={x['asset_id']:x for x in d.get('items',[])}
    if not delivery_evidence or not user_evidence or not d.get('evidence') or any(delivered.get(x['asset_id'])!=x for x in actual):
        raise ValueError('Deliver current actual gallery before user image review')
    event=check(user_evidence,d['recorded_at'],'images');wf=p.setdefault('workflow',{})
    retained={x['asset_id']:x for x in wf.get('image_approval',{}).get('items',[])}
    for x in actual:retained[x['asset_id']]=x|{'user_event':event,'user_evidence':event['text'],'delivered_at':d['recorded_at'],'recorded_at':now()}
    wf['image_approval']={'status':'approved','manifest_digest':digest(actual),'items':list(retained.values()),
       'gallery_path':str(gallery),'gallery_sha256':snap['panel_sha256'],'delivery_evidence':d['evidence'],
       'user_evidence':event['text'],'user_event':event,'delivered_at':d['recorded_at'],'recorded_at':now()}

def images_ready(p,base):
    text_ready(p,base)
    from review_state import asset_ready
    assets={a['id']:a for a in p.get('assets',[])}
    for item in image_manifest(p,base):asset_ready(p,assets[item['asset_id']])
    return True

def approve_video(p,base,preview_digest,user_evidence,delivery_receipt=None):
    images_ready(p,base)
    if not preview_digest or not user_evidence:raise ValueError('Video start approval must identify the delivered video preview')
    if not delivery_receipt or delivery_receipt.get('snapshot_digest')!=preview_digest or not delivery_receipt.get('evidence'):raise ValueError('Current actual video preview delivery receipt required')
    from review_evidence import check
    event=check(user_evidence,delivery_receipt['delivered_at'],'video')
    p['workflow']['video_approval']={'status':'approved','preview_digest':preview_digest,'user_evidence':event['text'],'user_event':event,'delivered_at':delivery_receipt['delivered_at'],'recorded_at':now()}

def before_video(p,base,preview_digest):
    confirmed(p);images_ready(p,base);r=p.get('workflow',{}).get('video_approval',{})
    if r.get('status')!='approved' or not r.get('user_evidence') or r.get('preview_digest')!=preview_digest:
        raise ValueError('Video preview/start awaiting user approval; text/image approval is not video approval')
    from review_evidence import verify_record
    verify_record(r,'video')
    from audit_project_network import require_clean
    require_clean(base)


def content_digest(p):
    import copy
    q=copy.deepcopy(p)
    # Review timestamps, receipts and progress do not change submitted content.
    q['workflow']={'production_scope':q.get('workflow',{}).get('production_scope',{})}
    for key in ('iterations','adaptation_review','progress','updated_at','checked_at') :q.pop(key,None)
    for item in q.get('assets',[]):
        for key in ('review','spatial_review','video_reference_review','progress','updated_at'):item.pop(key,None)
    for item in q.get('groups',[]):item.pop('review',None)
    return digest(q)
