"""Readable offline preview UI, shared by text and execution review stages."""
import datetime
import html
import io
import json
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote
from core import resolve, sha

PANEL = '全剧跑前预览.html'
SIDECARS = ('compiled_jobs.json', 'quote_snapshot.json', 'run_scope.json',
            'preview_checks.json', 'timing_plan.json', 'text_review_snapshot.json')


def esc(value):
    return html.escape(str(value if value is not None else '待准备'), quote=True)


def value_text(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value if value is not None else '待准备')


def details(title, body, css=''):
    return '<details class="'+esc(css)+'"><summary>'+esc(title)+'</summary>'+body+'</details>'


def archive_current(out):
    """Keep historical delivery files while allowing a stable current entry."""
    out = Path(out)
    if not (out/PANEL).is_file():
        return None
    folder = out/'history'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    for name in (PANEL,)+SIDECARS:
        src = out/name
        if src.is_file():
            if name == PANEL:
                text = src.read_text(encoding='utf-8')
                # The immutable image files are shared; do not duplicate hundreds
                # of base64 thumbnails or full-size files on every refresh.
                text = text.replace('src="preview_resources/', 'src="../../preview_resources/')
                text = text.replace('href="preview_resources/', 'href="../../preview_resources/')
                (folder/name).write_text(text, encoding='utf-8')
            else:
                shutil.copy2(src, folder/name)
    return folder


def image_resource(base, out, asset):
    """Copy each content-addressed original/thumbnail once. Never fetch URLs."""
    try:
        if not asset.get('local_path'):
            return {'present':False, 'reason':'图片待生成或待登记'}
        source = resolve(base, asset['local_path'])
        actual = sha(source)
        from PIL import Image
        with Image.open(source) as opened:
            opened.load()
            im = opened.convert('RGB')
            width, height = opened.size
        target = Path(out)/'preview_resources'
        target.mkdir(exist_ok=True)
        suffix = source.suffix.lower() or '.img'
        original = target/(actual+suffix)
        thumb = target/(actual+'-thumb.jpg')
        if not original.exists():
            shutil.copy2(source, original)
        if not thumb.exists():
            im.thumbnail((420, 300))
            im.save(thumb, 'JPEG', quality=82)
        return {'present':True, 'sha256':actual, 'width':width, 'height':height,
                'original':'preview_resources/'+quote(original.name),
                'thumbnail':'preview_resources/'+quote(thumb.name),
                'hash_matches':not asset.get('sha256') or asset['sha256'] == actual}
    except (OSError, ValueError, TypeError) as exc:
        return {'present':False, 'reason':'本地图片不可用：'+str(exc)}


def image_card(number, binding, asset, resource):
    image = '<div class="pending">'+esc(resource.get('reason', '图片待准备'))+'</div>'
    if resource.get('present'):
        image = '<a href="'+resource['original']+'" target="_blank" rel="noopener" title="查看原图"><img loading="lazy" src="'+resource['thumbnail']+'" alt="'+esc(binding.get('name', asset.get('name', '参考图'))) +'"></a>'
    remote = '已有远端地址，核对其有效期' if asset.get('remote_url') else '未上传／未登记远端地址'
    return ('<figure class="ref">'+image+'<figcaption><b>图'+str(number)+'</b> '+esc(binding.get('name',asset.get('name','')))+
            '<span>'+esc(binding.get('asset_id'))+' · '+esc(asset.get('version', asset.get('state_key', '当前登记版')))+
            '</span><span class="execution-only">'+esc(remote)+'</span></figcaption></figure>')


def scene_text(scene):
    return '｜'.join(str(v) for v in (scene.get('id'),scene.get('name'),scene.get('time'),scene.get('transition')) if v)


def state_text(shot, end=False):
    keys = ('exit_state','state_out') if end else ('entry_state','state_in')
    for key in keys:
        if shot.get(key):
            return value_text(shot[key])
    return shot.get('action') or '状态待补'


def sound_cell(shot):
    lines = []
    for speech in shot.get('speech', []):
        meta = '；'.join(str(v) for v in (speech.get('channel'),speech.get('emotion'),speech.get('delivery'),speech.get('tone')) if v)
        lines.append('<div class="speech"><b>'+esc(speech.get('speaker','人物待核对'))+'</b><span class="meta">【'+esc(meta or '声道／情绪待补')+'】</span>：“'+esc(speech.get('text',speech.get('spoken_text','')))+'”</div>')
    effects = shot.get('sound_effects', [])
    if isinstance(effects, str):
        effects = [effects]
    for effect in effects:
        if isinstance(effect, dict):
            text = effect.get('description') or effect.get('name') or effect.get('text') or value_text(effect)
        else:
            text = effect
        lines.append('<div class="fx">必要物理音效：'+esc(text)+'</div>')
    return ''.join(lines) or '<span class="muted">无登记对白／必要音效</span>'


CSS = r'''
:root{--ink:#18202e;--sub:#657083;--line:#dfe5ef;--bg:#f5f7fa;--accent:#315bea}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.wrap{display:flex;align-items:flex-start}nav{position:sticky;top:0;width:215px;flex:0 0 215px;height:100vh;overflow:auto;background:white;border-right:1px solid var(--line);padding:20px 12px}nav h2{font-size:15px;margin:0 8px 12px}nav a{display:block;padding:7px 9px;border-radius:7px;font-size:13px;color:var(--ink)}nav a:hover{background:#eef2ff}nav small{display:block;color:var(--sub);font-size:11px}main{min-width:0;width:100%;max-width:1400px;padding:26px 32px 90px}h1{font-size:27px;margin:0 0 5px}h2{font-size:22px;margin:0 0 6px}h3{font-size:17px;margin:0}p{margin:6px 0 14px}.lead,.meta,.muted{color:var(--sub)}.panel,.card{background:white;border:1px solid var(--line);border-radius:12px;margin:18px 0;overflow:hidden}.panel{padding:18px 20px}.stats{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.stat{border:1px solid var(--line);background:white;padding:9px 14px;border-radius:9px;min-width:128px}.stat b{display:block;font-size:20px;line-height:1.4}.stat span{font-size:12px;color:var(--sub)}.warn{color:#8b530a;background:#fff8e9;padding:8px 11px;border:1px solid #eed7a8;border-radius:7px}.ok{color:#286544}.chip{border:1px solid var(--line);background:white;border-radius:20px;padding:2px 10px;font-size:12px;display:inline-block;margin:2px 5px 2px 0}.ep{padding-top:28px;border-top:1px solid var(--line);margin-top:28px;scroll-margin-top:12px}.chd{padding:14px 18px;background:#f7f9ff;border-bottom:1px solid var(--line)}.chd .sub{font-size:13px;color:var(--sub)}.cbd{padding:16px 18px}.bind{background:#f5f7ff;border:1px dashed #b7c4ec;border-radius:8px;padding:8px 11px;font:13px/1.7 ui-monospace,Consolas,monospace;overflow-wrap:anywhere}.refs{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.ref{margin:0;width:180px}.ref img,.ref .pending{width:180px;height:108px;object-fit:contain;border:1px solid var(--line);border-radius:7px;background:#f7f8fa}.pending{display:flex;align-items:center;padding:9px;font-size:12px;color:var(--sub)}figcaption{font-size:12px;line-height:1.5}figcaption span{display:block;color:var(--sub)}.kv{display:grid;grid-template-columns:88px minmax(0,1fr);gap:4px 10px;font-size:13px;margin:10px 0}.kv dt{color:var(--sub)}.kv dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;background:white}th,td{border:1px solid var(--line);padding:8px 9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#f2f5fa;white-space:nowrap}.shots th:nth-child(1),.shots td:nth-child(1){width:7%}.shots th:nth-child(2),.shots td:nth-child(2){width:6%;white-space:nowrap}.shots th:nth-child(3){width:22%}.shots th:nth-child(4){width:36%}.shots th:nth-child(5){width:29%}.unused{opacity:.5;background:#fafafa}.scene{display:block;color:#626c7d;font-size:11.5px;margin-bottom:5px}.speech{margin-bottom:8px}.fx{color:#7d681f;margin-top:5px}.bridge{white-space:pre-wrap;font-size:13px;color:var(--sub);margin:11px 0}.endframe{border-left:3px solid #b7c4ec;padding-left:9px}details{border:1px solid var(--line);border-radius:9px;background:#fafbfe;padding:10px 13px;margin:12px 0}summary{cursor:pointer;font-size:13px;font-weight:600}details[open] summary{margin-bottom:9px}pre{font:12px/1.6 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:4px 0}.views{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.views label{cursor:pointer;border:1px solid var(--line);border-radius:8px;background:white;padding:7px 13px}.view-radio{position:absolute;opacity:0;width:1px;height:1px}.execution-only{display:none!important}#view-execution:checked~.wrap .execution-only{display:block!important}#view-execution:checked~.wrap .local-only{display:none!important}#view-local:checked~.wrap label[for=view-local],#view-execution:checked~.wrap label[for=view-execution]{background:#eaf0ff;color:#234bc6;border-color:#aabced}.foot{font-size:12px;color:var(--sub);margin-top:25px}code{font-size:12px;overflow-wrap:anywhere}@media(max-width:850px){nav{width:155px;flex-basis:155px;padding:12px 6px}main{padding:18px 14px 60px}.ref{width:145px}.ref img,.ref .pending{width:145px;height:90px}.shots{min-width:850px}.kv{grid-template-columns:70px minmax(0,1fr)}}@media print{nav,.views{display:none}main{max-width:none;padding:0}.card{break-inside:avoid}.execution-only{display:none!important}}
'''


CSS += '.shots td:first-child{white-space:nowrap;font-size:12px}'


def document(title, sidebar, body):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+CSS+'</style></head><body>'
            '<input class="view-radio" id="view-local" name="preview-view" type="radio" checked>'
            '<input class="view-radio" id="view-execution" name="preview-view" type="radio">'
            '<div class="wrap"><nav aria-label="逐集导航"><h2>逐集跑前预览</h2>'+sidebar+'</nav><main>'+body+'</main></div></body></html>')
