"""Create two empty progress entrances before creative/media work; never call an API."""
import argparse,json
from pathlib import Path
from core import load,save
from extract_source import extract
from refresh_progress import refresh

def initialize(directory,source,project_id=None):
    base=Path(directory);base.mkdir(parents=True,exist_ok=True);pp=base/'project.json'
    if pp.exists():raise ValueError('Project exists; use refresh_progress.py to resume without overwriting')
    p=load(Path(__file__).resolve().parents[1]/'assets'/'project.template.json')
    p['project_id']=project_id or base.name;p['source_path']='source.json';p['input_source_path']=str(Path(source).resolve())
    if (base/'source.json').exists():raise ValueError('Source extraction output exists')
    save(base/'source.json',extract(source));save(pp,p)
    result=refresh(pp);result['project']=str(pp);return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('directory');ap.add_argument('source');ap.add_argument('--project-id');a=ap.parse_args()
    print(json.dumps(initialize(a.directory,a.source,a.project_id),ensure_ascii=False))
