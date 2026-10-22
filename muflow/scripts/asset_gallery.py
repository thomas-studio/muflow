"""Offline, lineage-grouped asset delivery; never generates, uploads or grants approval."""
import argparse,datetime,html,json,os,shutil,sqlite3
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import quote
from core import load,save,resolve,sha,digest,safe_name

GALLERY='00_资产参考图总览.html'
SNAPSHOT='资产图册快照.json'
KINDS={'role':'人物与阶段','extra':'配角','group':'群像','scene':'场景','prop':'道具','vehicle':'载具'}
SECTIONS={'people':'人物、配角与群像','scene':'场景','prop':'道具','vehicle':'载具','other':'其他资产'}

def required_ids(project):
    from workflow_gate import scope_groups
    assets={a['id']:a for a in project.get('assets',[])}
    scope=project.get('workflow',{}).get('production_scope') or project.get('production_scope')
    if not project.get('episodes') and not project.get('groups') and not scope:return set()
    ids={b['asset_id'] for g in scope_groups(project) for b in g.get('bindings',[])}
    pending=list(ids)
    while pending:
        aid=pending.pop()
        if aid not in assets:raise ValueError('Unknown gallery asset '+aid)
        parent=assets[aid].get('parent_id')
        if parent and parent not in ids:ids.add(parent);pending.append(parent)
    return ids

def _lineages(project):
    assets={a['id']:a for a in project.get('assets',[])};parents={aid:aid for aid in assets}
    def find(x):
        while parents[x]!=x:x=parents[x]
        return x
    def union(x,y):parents[find(y)]=find(x)
    entities=project.get('entities',{});entities=entities if isinstance(entities,dict) else {e['id']:e for e in entities}
    def identity(eid):
        seen=set();cur=eid
        while cur in entities and cur not in seen:
            seen.add(cur);nxt=entities[cur].get('identity_id')
            if not nxt or nxt==cur:break
            cur=nxt
        return str(cur)
    identities={};depth={};chains={}
    for aid,a in assets.items():
        chain=[];cur=aid
        while cur:
            if cur in chain:raise ValueError('Gallery identity lineage cycle: '+aid)
            if cur not in assets:raise ValueError('Unknown gallery lineage parent '+cur)
            chain.append(cur);cur=assets[cur].get('parent_id')
        chains[aid]=list(reversed(chain));depth[aid]=len(chain)-1
        if a.get('parent_id'):union(aid,a['parent_id'])
        if a.get('entity_id'):
            key=identity(a['entity_id'])
            if key in identities:union(aid,identities[key])
            else:identities[key]=aid
    members={}
    for aid in assets:members.setdefault(find(aid),[]).append(aid)
    result={}
    for group in members.values():
        roots=[aid for aid in group if not assets[aid].get('parent_id')]
        entity=sorted({identity(assets[x]['entity_id']) for x in group if assets[x].get('entity_id')})
        key='entity:'+','.join(entity) if entity else 'lineage:'+min(roots or group)
        for aid in group:result[aid]={'group_key':key,'identity_lineage':chains[aid],'lineage_depth':depth[aid]}
    return result

def _first_uses(project):
    uses={}
    for gi,g in enumerate(project.get('groups',[])):
        for si,s in enumerate(g.get('shots',[])):
            eps=[int(u['episode']) for u in g.get('uses',[]) if not u.get('shot_ids') or s.get('id') in u['shot_ids']]
            ep=min(eps or [int(g.get('episode',10**9))]);rank=[ep,gi,si]
            aids=list(s.get('asset_ids',[]))
            for state in ('state_in','state_out'):
                aids.extend(e.get('reference_asset_id') for e in s.get(state,{}).get('entities',{}).values())
            for aid in aids:
                if aid and (aid not in uses or rank<uses[aid]['rank']):uses[aid]={'rank':rank,'episode':ep,'group_id':g.get('id'),'shot_id':s.get('id')}
        for b in g.get('bindings',[]):
            aid=b.get('asset_id')
            if aid not in uses:
                ep=min([int(u['episode']) for u in g.get('uses',[])] or [int(g.get('episode',10**9))])
                uses[aid]={'rank':[ep,gi,10**9],'episode':ep,'group_id':g.get('id'),'shot_id':None}
    return uses

def _jobs(base):
    """Read only; never create a production database or rewrite provider receipts."""
    db=Path(base)/'production.sqlite'
    if not db.is_file():return {},None
    try:
        conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:rows=conn.execute("SELECT id,data FROM jobs WHERE id LIKE 'image:%'").fetchall()
        finally:conn.close()
        return {k[6:]:json.loads(v) for k,v in rows},None
    except (sqlite3.Error,ValueError) as e:return {},'本地任务状态不可读：'+type(e).__name__

def _image(base,path,expected,label):
    if not path:return False
    p=resolve(base,path)
    if not p.is_file():return False
    if not expected or sha(p)!=expected:raise ValueError('Gallery source hash changed: '+label)
    from PIL import Image
    with Image.open(p) as im:im.verify()
    return True

def _progress(a,present,job,read_error):
    review=a.get('review',{});phase=job.get('phase') or a.get('generation_status') or a.get('phase')
    updated=job.get('updated_at') or job.get('recorded_at') or a.get('updated_at') or a.get('generated_at')
    known={'not_started':'未开始','pending':'未开始','ready':'未开始','queued':'生成中','running':'生成中','submitting':'生成中','result_ready':'返回待登记','saved':'返回待登记','failed':'失败','uncertain':'待核实','blocked':'待核实'}
    if read_error or (a.get('local_path') and not present):status='待核实'
    elif present and (not phase or phase=='saved'):
        status='待审'
    else:status=known.get(phase,'待核实')
    return {'status':status,'phase':phase,'updated_at':updated,'evidence':read_error or job.get('error') or ('已核对本地图片与登记哈希' if present else '待生成／待登记实际图片；无明确任务证据时不推断未开始'),
            'generation_review_status':job.get('review_status') or a.get('generation_review_status') or '未记录'}

def manifest(project,base):
    base=Path(base);ids=required_ids(project);cards=[];outside=[];versions=[];traces=[]
    lineage=_lineages(project);uses=_first_uses(project);jobs,read_error=_jobs(base)
    for a in project.get('assets',[]):
        row={k:a.get(k) for k in ('id','name','kind','version','mode','parent_id','prompt','entity_id','state_key','edit_audit','user_review','user_approval_evidence')}
        row.update(lineage[a['id']]);row['first_use']=uses.get(a['id']);row['review']=a.get('review',{});row['path']=a.get('local_path');row['sha256']=a.get('sha256')
        row['present']=_image(base,row['path'],row['sha256'],a['id']);row['progress']=_progress(a,row['present'],jobs.get(a['id'],{}),read_error)
        if a['id'] in ids:cards.append(row)
        elif row['present'] or row['path'] or row['sha256']:outside.append(row)
        for suffix in ('_request_audit.json','_result_audit.json'):
            path=base/'99_工作区'/'生图原始记录'/(safe_name(a['id'])+suffix)
            if path.is_file():traces.append({'asset_id':a['id'],'path':str(path.relative_to(base)),'sha256':sha(path),'label':'原始请求' if 'request' in suffix else '原始回执'})
    for v in project.get('iterations',[]):
        if v.get('kind')!='image':continue
        if not _image(base,v.get('local_path'),v.get('sha256'),str(v.get('id'))):raise ValueError('Iteration file absent/changed: '+str(v.get('id')))
        if v.get('status')=='用户采用' and not v.get('user_approval_evidence'):raise ValueError('Agent review cannot declare user adoption')
        row={k:v.get(k) for k in ('id','asset_id','label','status','reason','local_path','sha256','version','parent_id','prompt')}
        row['record']=v;versions.append(row)
    if len({a['id'] for a in project.get('assets',[])})!=len(project.get('assets',[])):raise ValueError('Duplicate gallery asset id')
    if len({v['id'] for v in versions})!=len(versions):raise ValueError('Duplicate gallery iteration id')
    return {'project_id':project['project_id'],'project_version':project['version'],'cards':cards,'versions':versions,'outside_cards':outside,
            'iterations':project.get('iterations',[]),'trace_files':traces,'review_mode':project.get('settings',{}).get('asset_review_mode'),
            'scope':project.get('workflow',{}).get('production_scope',{}),'meaning':'候选/智能体复核与用户采用分别记录；未生成项保留占位，图册本身不代表批准'}

def copy_exact(src,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if sha(src)!=sha(dest):raise ValueError('Delivery filename already belongs to a different image version: '+dest.name)
    else:shutil.copyfile(src,dest)
    if sha(src)!=sha(dest):raise ValueError('Asset delivery copy hash mismatch')

def _section(kind):return 'people' if kind in ('role','extra','group') else kind if kind in SECTIONS else 'other'
def _rank(a):return (a.get('first_use') or {}).get('rank',[10**9,10**9,10**9])
def _groups(cards):
    groups={}
    for a in cards:groups.setdefault((_section(a['kind']),a['group_key']),[]).append(a)
    result=[]
    for (section,key),items in groups.items():
        # A mother precedes her derivatives even if the first direct on-screen use is later.
        ordered=[];pending=list(items);all_ids={a['id'] for a in items}
        while pending:
            ready=[a for a in pending if not a.get('parent_id') or a['parent_id'] not in all_ids or any(x['id']==a['parent_id'] for x in ordered)]
            a=min(ready,key=lambda x:(bool(x.get('parent_id')),_rank(x),x['id']));ordered.append(a);pending.remove(a)
        result.append({'section':section,'key':key,'items':ordered,'rank':min((_rank(a) for a in items))})
    return sorted(result,key=lambda g:(list(SECTIONS).index(g['section']),g['rank'],g['key']))

def _plan(m,base,out):
    images=[];current={};hist={};asset_cards=m['cards']+m.get('outside_cards',[])
    for a in asset_cards:
        if not a['present']:continue
        src=resolve(base,a['path']);label=a.get('version') or 'v1'
        name=safe_name(a['id'])+'_'+safe_name(a['name'])+'_'+safe_name(label)+'_'+a['sha256'][:12]+src.suffix.lower()
        r={'key':'current:'+a['id'],'asset_id':a['id'],'file':name,'sha256':a['sha256'],'source':str(src),'current':True}
        current[(a['id'],a['sha256'])]=r;images.append(r)
    by_bytes={}
    for v in m['versions']:
        # Missing asset_id is legacy evidence, never guessed from a Chinese label or shared pixels.
        key=(v.get('asset_id'),v['sha256']);r=current.get(key) or by_bytes.get(key)
        if r is None:
            src=resolve(base,v['local_path']);name=safe_name(v.get('asset_id') or v['id'])+'_'+safe_name(v.get('label') or v['id'])+'_'+v['sha256'][:12]+src.suffix.lower()
            r={'key':'history:'+str(v.get('asset_id') or v['id'])+':'+v['sha256'],'asset_id':v.get('asset_id'),'file':'../99_工作区/图片历史/'+name,'sha256':v['sha256'],'source':str(src),'current':False}
            images.append(r);by_bytes[key]=r
        hist[v['id']]=r
    return images,current,hist

def build(project_path):
    pp=Path(project_path);p=load(pp);base=pp.parent;out=base/'03_资产图';out.mkdir(exist_ok=True)
    m=manifest(p,base);esc=lambda v:html.escape(str(v if v is not None else ''),quote=True)
    images,current,hist=_plan(m,base,out);copies=[];history_images=[]
    for r in images:
        copy_exact(Path(r['source']),out/r['file'])
        if r['current']:copies.append({'file':r['file'],'sha256':r['sha256']})
    for r in hist.values():
        row={'file':r['file'],'sha256':r['sha256']}
        if row not in history_images:history_images.append(row)
    def img(r,name,current_id=None):
        attrs=' data-asset-id="'+esc(current_id)+'"' if current_id else ''
        url=esc(quote(r['file']));return '<a href="'+url+'"><img loading="lazy" decoding="async" data-image-key="'+esc(r['key'])+'"'+attrs+' src="'+url+'" alt="'+esc(name)+'"></a>'
    def versions(aid):
        rows=[v for v in m['versions'] if v.get('asset_id')==aid];parts=[];shown=set()
        for v in rows:
            r=hist[v['id']];picture=''
            if not r['current'] and r['key'] not in shown:picture=img(r,v.get('label'));shown.add(r['key'])
            parts.append('<section data-history-id="'+esc(v['id'])+'"><h4>'+esc(v.get('label') or v['id'])+'</h4><p>登记时状态：'+esc(v.get('status') or '未记录')+' · 原因：'+esc(v.get('reason') or '未记录')+'</p>'+picture+'<a href="'+esc(quote(r['file']))+'">'+('查看上方当前原图（同ID、同SHA，不重复缩略图）' if r['current'] else '打开此版原图')+'</a><pre>'+esc(json.dumps(v['record'],ensure_ascii=False,indent=2))+'</pre></section>')
        return '<details class="history"><summary>已登记尝试与旧候选（'+str(len(rows))+'条记录）</summary>'+''.join(parts)+'</details>' if rows else ''
    def display_status(a):
        if a['progress']['status']!='待审':return a['progress']['status']
        from review_state import asset_ready
        try:asset_ready(p,a);return '已审核'
        except (ValueError,OSError,KeyError):return '待审'
    def card(a,outside=False):
        from review_state import human_asset_record
        a=dict(a);a['user_adoption']=human_asset_record(p,a)
        aid=a['id'];label=a.get('version') or 'v1';r=current.get((aid,a.get('sha256')));content=img(r,a['name'],aid) if r else '<p class="pending">待生成／待登记实际图片</p>'
        attr='data-outside-id' if outside else 'data-current-id';first=a.get('first_use');first_text=('第'+str(first['episode'])+'集 · '+str(first.get('group_id'))+' · '+str(first.get('shot_id') or '组绑定')) if first else '未记录剧情首次使用'
        user=a.get('user_adoption') or a.get('user_review') or {};user_status=('用户采用（绑定当前内容）' if a.get('user_adoption') else user.get('status')) or ('已有用户证据，结论见原记录' if a.get('user_approval_evidence') else '未记录用户采用')
        links=[]
        for t in m['trace_files']:
            if t['asset_id']==aid:links.append('<a href="'+esc(quote(Path(os.path.relpath(resolve(base,t['path']),out)).as_posix()))+'">'+esc(t['label'])+'</a>')
        audit=a.get('edit_audit');audit_text=json.dumps(audit,ensure_ascii=False,indent=2) if audit else '未记录实际母图编辑审计；归组不代表已实际传入母图'
        return '<article id="asset-'+esc(aid)+'" '+attr+'="'+esc(aid)+'" data-search="'+esc(' '.join(str(a.get(k) or '') for k in ('id','name','kind','entity_id','state_key')))+'"><h3>'+esc(aid)+' · '+esc(a['name'])+'</h3><p>'+esc(KINDS.get(a['kind'],a['kind']))+' · '+esc(label)+' · 当前进度：'+esc(display_status(a))+'</p>'+content+'<p>母图：'+esc(a.get('parent_id') or '主状态／原图')+' · 身份：'+esc(a.get('entity_id') or '未记录')+'<br>首次使用：'+esc(first_text)+'</p><dl><dt>AI当前结论</dt><dd>'+esc(a['review'].get('status') or '待复核')+' · '+esc(a['review'].get('evidence') or '未记录')+'</dd><dt>用户采用</dt><dd>'+esc(user_status)+' · '+esc(user.get('user_evidence') or user.get('evidence') or a.get('user_approval_evidence') or '无用户采用证据')+'</dd><dt>生成时审核状态</dt><dd>'+esc(a['progress']['generation_review_status'])+'</dd><dt>任务状态与更新时间</dt><dd>'+esc(a['progress']['phase'] or '未记录')+' · '+esc(a['progress']['updated_at'] or '未记录')+'<br>'+esc(a['progress']['evidence'])+'</dd></dl><details><summary>实际提示词、身份链与原始证据</summary><pre>'+esc(a['prompt'])+'</pre><p>身份链：'+esc(' → '.join(a['identity_lineage']))+'</p><pre>'+esc(audit_text)+'</pre><p>'+' · '.join(links)+'</p></details>'+versions(aid)+'</article>'
    def sections(cards,outside=False):
        parts=[];section=None
        for g in _groups(cards):
            if section!=g['section']:
                if section is not None:parts.append('</section>')
                section=g['section'];parts.append('<section data-section="'+esc(section)+'"><h2>'+esc(SECTIONS[section])+'</h2>')
            parts.append('<div class="identity" data-identity-group="'+esc(g['key'])+'"><h3>身份／母图组：'+esc(g['key'])+'</h3><div class="grid">'+''.join(card(a,outside) for a in g['items'])+'</div></div>')
        if section is not None:parts.append('</section>')
        return ''.join(parts)
    all_display={a['id'] for a in m['cards']+m['outside_cards']};unknown={v.get('asset_id') for v in m['versions'] if v.get('asset_id') not in all_display}
    orphan=''.join('<details><summary>未绑定展示卡的原始记录：'+esc(aid or '旧记录未登记asset_id')+'</summary>'+versions(aid)+'</details>' for aid in sorted(unknown,key=lambda x:str(x)))
    raw_links=''.join('<li>'+esc(t['asset_id'])+' · <a href="'+esc(quote(Path(os.path.relpath(resolve(base,t['path']),out)).as_posix()))+'">'+esc(t['label'])+'</a></li>' for t in m['trace_files'])
    raw_index='<details><summary>全部原始请求与回执索引</summary><ul>'+raw_links+'</ul></details>'
    from review_state import readiness
    try:health=readiness(p,base)
    except (ValueError,OSError,KeyError) as e:health={'label':'待核实','evidence':[{'reason':str(e)}]}
    health_panel='<aside><h2>项目状态：'+esc(health.get('label'))+'</h2><p>统一按实际文本、结构、图片审核和问题复核记录判断；出图数量不代表验收通过，图册刷新不产生批准。</p><details><summary>当前依据与待复核项</summary><pre>'+esc(json.dumps(health,ensure_ascii=False,indent=2))+'</pre></details></aside>'
    built_at=datetime.datetime.now(datetime.timezone.utc).isoformat();mode={'manual':'人工审核','agent':'AI自动审核','joint':'人机联合审核'}.get(m['review_mode'],'待确认（未配置审核模式）')
    counts={}
    for a in m['cards']:
        status=display_status(a);counts[status]=counts.get(status,0)+1
    body='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>资产参考图总览</title><style>body{font:16px/1.6 system-ui;margin:24px;background:#f5f6f8;color:#20252b}main{max-width:1380px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}.identity{border:1px solid #aab7c5;padding:15px;margin:20px 0}article{padding:15px;background:white;border:1px solid #ddd;border-radius:10px}img{width:100%;max-height:340px;object-fit:contain}pre{white-space:pre-wrap;overflow-wrap:anywhere}.pending{padding:55px 10px;background:#eee}input{padding:10px;width:min(90%,550px)}h3{font-size:18px}details{margin:12px 0}dt{font-weight:600}dd{margin-left:0}a{overflow-wrap:anywhere}</style><main><h1>'+esc(p['project_id'])+' · 资产参考图总览</h1>'+health_panel+'<p>本次范围'+str(len(m['cards']))+'项，实际图片'+str(sum(a['present'] for a in m['cards']))+'项；范围外已生成'+str(sum(a['present'] for a in m['outside_cards']))+'项，另有缺图登记待核实'+str(sum(not a['present'] for a in m['outside_cards']))+'项。'+esc('；'.join(k+' '+str(v) for k,v in counts.items()))+'。</p><p>图册更新时间：'+esc(built_at)+' · 审核模式：'+esc(mode)+'。生成时状态、AI当前结论和用户采用分别记录；点击图片打开本地原图。</p><p><a href="00_生图说明与索引.md">使用说明</a> · <a href="当前候选图片清单.json">当前候选清单</a> · <a href="实际资产提示词.md">实际提示词</a> · <a href="全部迭代追溯.json">全部迭代原记录</a></p><input id="search" placeholder="搜索编号、中文名、身份、阶段或类别">'+sections(m['cards'])+'<section id="outside-scope"><h2>范围外已生成资产与缺图登记</h2><p>以下仍是各资产当前候选，不冒充旧版本，也不扩大本次执行必需范围。</p>'+sections(m['outside_cards'],True)+'</section>'+orphan+raw_index+'<script>document.getElementById("search").addEventListener("input",e=>document.querySelectorAll("[data-search]").forEach(c=>c.hidden=!c.dataset.search.toLowerCase().includes(e.target.value.toLowerCase())));</script></main></html>'
    (out/GALLERY).write_text(body,encoding='utf-8');save(out/'当前候选图片清单.json',m['cards']);save(out/'全部迭代追溯.json',m['iterations'])
    (out/'实际资产提示词.md').write_text('\n\n'.join('## '+a['id']+' · '+a['name']+'\n\n'+str(a['prompt'] or '复用已有图，无生图请求') for a in m['cards']+m['outside_cards']),encoding='utf-8')
    (out/'00_生图说明与索引.md').write_text('# 资产参考图\n\n[打开全部资产图册]('+GALLERY+')\n\n同身份按entity_id、identity_id与parent_id归组，母图在前，其余按剧情首次使用排序。场景、道具、载具分区。当前相同ID与SHA只展示一次；旧候选和每次原始登记在对应资产下折叠追溯。范围外已生成资产另列，不计本次执行必需范围。原图、原请求和回执保留；图册刷新不代表用户批准。没有任务证据的缺图显示待核实。\n',encoding='utf-8')
    snap={'manifest':m,'manifest_digest':digest(m),'panel_sha256':sha(out/GALLERY),'copied_images':copies,'history_images':history_images,
          'visible_images':[{k:r[k] for k in ('key','file','sha256','current','asset_id')} for r in images],'trace_files':m['trace_files'],'iterations_sha256':sha(out/'全部迭代追溯.json'),'built_at':built_at}
    save(out/SNAPSHOT,snap);return {'gallery':str(out/GALLERY),'assets':len(m['cards']),'present':sum(a['present'] for a in m['cards']),'versions':len(m['versions']),'outside_assets':len(m['outside_cards']),'external_requests':0}

class Cards(HTMLParser):
    def __init__(self):super().__init__();self.cards=[];self.images=[];self.history=[];self.outside=[];self.actual_images=[];self.links=[];self.stack=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'data-current-id' in a:self.cards.append(a['data-current-id'])
        if 'data-outside-id' in a:self.outside.append(a['data-outside-id'])
        if tag=='a':self.stack.append(a.get('href'))
        if tag=='img':
            self.actual_images.append({'key':a.get('data-image-key'),'src':a.get('src'),'href':self.stack[-1] if self.stack else None})
            if 'data-asset-id' in a:self.images.append(a['data-asset-id'])
        if 'data-history-id' in a:self.history.append(a['data-history-id'])
        if tag=='a':self.links.append(a.get('href'))
    def handle_endtag(self,tag):
        if tag=='a' and self.stack:self.stack.pop()

def verify(project,base,gallery):
    base=Path(base);f=resolve(base,gallery);snap=load(f.parent/SNAPSHOT);m=manifest(project,base)
    if snap.get('manifest_digest')!=digest(m) or snap.get('manifest')!=m or snap.get('panel_sha256')!=sha(f):raise ValueError('Asset gallery missing/stale or edited')
    if any(a.get('path') and not a['present'] for a in m['cards']+m['outside_cards']):raise ValueError('Registered gallery image missing; pending placeholder is not an actual image')
    parser=Cards();parser.feed(f.read_text(encoding='utf-8'));expected=[a['id'] for a in m['cards']];outside=[a['id'] for a in m.get('outside_cards',[])]
    images,_,_=_plan(m,base,f.parent)
    actual=sorted((x['key'] or '',x['src'] or '',x['href'] or '') for x in parser.actual_images)
    wanted=sorted((r['key'],quote(r['file']),quote(r['file'])) for r in images)
    if sorted(parser.cards)!=sorted(expected) or sorted(parser.outside)!=sorted(outside) or sorted(parser.images)!=sorted(a['id'] for a in m['cards']+m['outside_cards'] if a['present']) or sorted(parser.history)!=sorted(v['id'] for v in m['versions']) or actual!=wanted:raise ValueError('Asset gallery visible coverage incomplete')
    for r in images:
        if sha(f.parent/r['file'])!=r['sha256']:raise ValueError('Gallery image link changed or missing')
    for r in m['trace_files']:
        link=quote(Path(os.path.relpath(resolve(base,r['path']),f.parent)).as_posix())
        if link not in parser.links:raise ValueError('Gallery original evidence link missing')
        if sha(resolve(base,r['path']))!=r['sha256']:raise ValueError('Gallery original evidence changed')
    if load(f.parent/'全部迭代追溯.json')!=m['iterations'] or sha(f.parent/'全部迭代追溯.json')!=snap.get('iterations_sha256'):raise ValueError('Gallery iteration trace changed or missing')
    return snap

def register_saved(project_path,aid,result):
    if result.get('phase')!='saved':return
    from batch_video import exclusive
    pp=Path(project_path);base=pp.parent
    with exclusive(base/'.asset-registry.lock'):
        p=load(pp);a=next(x for x in p['assets'] if x['id']==aid);src=Path(result['output'])
        if sha(src)!=result['sha256']:raise ValueError('Saved image changed before registration')
        audit=result.get('audit',{})
        if a.get('parent_id'):
            parent=next((x for x in p['assets'] if x['id']==a['parent_id']),None)
            if not parent or audit.get('operation')!='image_edit' or audit.get('input_asset_id')!=parent['id'] or audit.get('input_sha256')!=parent.get('sha256') or audit.get('input_count')!=1 or sha(resolve(base,parent['local_path']))!=parent.get('sha256'):raise ValueError('Saved derivative requires actual unchanged mother image audit')
        changed=a.get('sha256')!=result['sha256'] or a.get('local_path')!=str(src)
        if changed and a.get('local_path') and a.get('sha256') and a.get('sha256')!=result['sha256']:
            prior=resolve(base,a['local_path'])
            if prior.is_file() and sha(prior)==a['sha256']:
                oldrow={'id':'image:'+aid+':'+a['sha256'][:12],'asset_id':aid,'kind':'image','label':a['name']+' · 历史候选','version':a.get('version','v1'),'local_path':str(prior),'sha256':a['sha256'],'status':'历史候选待复核','reason':'同资产已有旧图，新结果成为当前候选；原图保留，不代表用户退回','parent_id':a.get('parent_id'),'prompt':a.get('prompt'),'review_at_replacement':a.get('review',{}),'edit_audit':a.get('edit_audit')}
                if not any(x.get('id')==oldrow['id'] for x in p.setdefault('iterations',[])):p['iterations'].append(oldrow)
        a.update(local_path=str(src),sha256=result['sha256'])
        if audit.get('operation')=='image_edit':a['edit_audit']=audit
        if changed:
            a['review']={'status':'待复核','evidence':''};a['generation_review_status']=result.get('review_status') or '待复核'
        row={'id':'image:'+aid+':'+result['sha256'][:12],'asset_id':aid,'kind':'image','label':a['name']+' · '+str(a.get('version','v1')),'version':a.get('version','v1'),
             'local_path':str(src),'sha256':result['sha256'],'status':'待复核','reason':'接口已返回，等待实际看图；未代表用户采用','parent_id':a.get('parent_id'),'prompt':a.get('prompt'),'generation_review_status':result.get('review_status') or '待复核','audit':audit}
        if not any(x.get('id')==row['id'] for x in p.setdefault('iterations',[])):p['iterations'].append(row)
        save(pp,p);build(pp)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');a=ap.parse_args();print(json.dumps(build(a.project),ensure_ascii=False))
