"""Refresh fixed local entrances; never submits or uploads media."""
import argparse,json
from pathlib import Path
from core import load,save

def refresh(project_path,profile_path=None):
    pp=Path(project_path);base=pp.parent;out={'media_calls':0,'issues':[]}
    from asset_gallery import build as gallery
    from build_preview import build as preview
    try:out['assets']=gallery(pp)
    except Exception as exc:out['issues'].append({'object':'asset_gallery','result':'待复核','error':str(exc)})
    try:out['preview']=preview(pp,base/'07_跑前预览',profile_path)
    except Exception as exc:out['issues'].append({'object':'run_preview','result':'待复核','error':str(exc)})
    p=load(pp)
    from selfcheck_report import build as report
    review_report=base/'03_资产图'/'资产审核问题汇总.md';review_report.parent.mkdir(parents=True,exist_ok=True)
    review_report.write_text(report([pp]),encoding='utf-8');out['review_report']=str(review_report)
    out['asset_table']=p.get('deliverables',{}).get('asset_table')
    save(base/'99_工作区'/'面板刷新状态.json',out)
    return out

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('--profile');a=ap.parse_args()
    print(json.dumps(refresh(a.project,a.profile),ensure_ascii=False))
