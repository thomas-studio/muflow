"""Check explicitly recorded source-to-shot scene bindings, never image semantics."""
from pathlib import Path
from core import load, resolve, sha

DIMENSIONS=('location_id','space_id','inout','time')

def audit(project, base, groups):
    """Return (code, object, message) tuples for the requested groups only.

    A declared scene identity is structured provenance, not proof that the
    pixels depict that location; independent actual image review is mandatory.
    """
    errors=[]
    def fail(code,obj,msg): errors.append((code,str(obj),msg))
    baseline=project.get('scene_binding_baseline')
    if not isinstance(baseline,dict) or not str(baseline.get('evidence','')).strip():
        return [('SCENE_BASELINE','project','缺少独立场次基准及其来源依据，待复核')]
    try:
        source=resolve(base,project['source_path'])
        if baseline.get('source_sha256')!=sha(source):
            return [('SCENE_BASELINE','project','独立场次基准的来源文件哈希缺失或已变化')]
        source_ids={p['id'] for p in load(source)['paragraphs']}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return [('SCENE_BASELINE','project','原稿来源不可核对：'+str(exc))]
    entries=baseline.get('scenes')
    if not isinstance(entries,list): return [('SCENE_BASELINE','project','缺少场次基准清单')]
    index={}
    used={shot.get('scene_id') for group in groups for shot in group.get('shots',[])}
    for row in entries:
        if not isinstance(row,dict):
            fail('SCENE_BASELINE','project','场次基准记录不是对象'); continue
        key=row.get('scene_id')
        if key not in used: continue
        if not key or key in index:
            fail('SCENE_BASELINE',key or 'project','场次基准编号缺失或重复');continue
        index[key]=row
    scenes={s.get('id'):s for s in project.get('scenes',[])}
    assets={a.get('id'):a for a in project.get('assets',[])}
    for group in groups:
        bound={b.get('asset_id') for b in group.get('bindings',[])}
        for shot in group.get('shots',[]):
            sid=shot.get('id','unknown');scene=scenes.get(shot.get('scene_id'));row=index.get(shot.get('scene_id'))
            if not scene or not row:
                fail('SCENE_BASELINE',sid,'镜头缺少来源场次或独立基准');continue
            if any(not str(row.get(k,'')).strip() for k in ('scene_name','source_id','location_id','space_id','inout','time','evidence')):
                fail('SCENE_BASELINE',sid,'场次基准缺少来源、位置、内外、时间或依据');continue
            if (str(row.get('episode'))!=str(scene.get('episode')) or row['scene_name']!=scene.get('name')
                or row['source_id']!=scene.get('source_id') or row['source_id'] not in source_ids
                or row['inout']!=scene.get('inout') or row['time']!=scene.get('time')):
                fail('SCENE_BASELINE',sid,'场次基准与来源场次编号、名称、内外或时间不一致');continue
            aid=shot.get('primary_scene_asset_id');allowed=row.get('allowed_scene_asset_ids')
            if not isinstance(allowed,list) or not allowed or not aid or aid not in allowed:
                fail('SCENE_PRIMARY',sid,'缺少本镜主场景资产或不属于场次基准的允许版本');continue
            asset=assets.get(aid)
            if not asset or asset.get('kind')!='scene':
                fail('SCENE_PRIMARY',sid,'本镜主场景资产不存在或类型不是场景');continue
            if aid not in shot.get('asset_ids',[]) or aid not in bound:
                fail('SCENE_BINDING',sid,'本镜主场景未同时进入逐镜引用和本组实际参考图顺序');continue
            ident=asset.get('scene_identity')
            if not isinstance(ident,dict) or not str(ident.get('evidence','')).strip():
                fail('SCENE_IDENTITY',sid,'场景素材身份或核对依据缺失');continue
            if any(ident.get(k)!=row.get(k) for k in DIMENSIONS):
                fail('SCENE_IDENTITY',sid,'场景素材的地点、子空间、内外或日夜与来源场次不符');continue
            secondary=shot.get('secondary_scene_asset_ids',[])
            if not isinstance(secondary,list) or len(secondary)!=len(set(secondary)) or aid in secondary:
                fail('SCENE_SECONDARY',sid,'背景/过渡场景资产列表无效');continue
            actual_other={a for a in shot.get('asset_ids',[]) if a!=aid and assets.get(a,{}).get('kind')=='scene'}
            if set(secondary)!=actual_other or any(x not in bound for x in secondary):
                fail('SCENE_SECONDARY',sid,'其他入镜场景须逐一声明为背景或过渡并实际绑定');continue
            if secondary and not str(shot.get('secondary_scene_evidence','')).strip():
                fail('SCENE_SECONDARY',sid,'背景/过渡场景须明确空间关系及依据')
    return errors
