"""Flag project-local network code. This is an entry-point audit, not a shell firewall."""
import argparse,json,re
from pathlib import Path
from core import save

PATTERN=re.compile(r'\b(?:import\s+(?:urllib|requests|httpx|aiohttp|http\.client)|from\s+(?:urllib|requests|httpx|aiohttp)|urllib\.request|requests\.(?:post|request)|fetch\s*\(|axios\.|Invoke-WebRequest|Invoke-RestMethod|curl\b|wget\b)',re.I)

def audit(base):
    base=Path(base);rows=[]
    for f in base.rglob('*'):
        if not f.is_file() or f.suffix.lower() not in ('.py','.js','.ts','.sh','.ps1','.bat','.cmd'):continue
        rel=f.relative_to(base)
        if set(rel.parts)&{'.git','.venv','node_modules','03_资产图'}:continue
        if '停用脚本' in rel.parts:continue
        text=f.read_text(encoding='utf-8-sig',errors='replace')
        hits=[i+1 for i,s in enumerate(text.splitlines()) if PATTERN.search(s)]
        if hits:rows.append({'file':rel.as_posix(),'lines':hits,'status':'不通过','reason':'项目临时脚本含独立联网能力；接口联调/样片不能绕过指定配套入口和阶段批准'})
    return {'execution_status':'已执行','status':'不通过' if rows else '通过','issues':rows,'limit':'只检查当前登记项目文件，不能阻止宿主不运行检查就执行新代码，也不能替代凭据/服务端权限隔离'}

def require_clean(base):
    report=audit(base)
    if report['issues']:raise ValueError('Uncontrolled project network script: '+str(report['issues'][0]))
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project_dir');ap.add_argument('--out');a=ap.parse_args();r=audit(a.project_dir)
    if a.out:save(a.out,r)
    print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(bool(r['issues']))
