"""Record real user-stage evidence, never fabricate it from an agent decision."""
import argparse,json
from pathlib import Path
from core import load,save,digest
from workflow_gate import *

def record(project_path,action,evidence,preview=None,gallery=None,delivery_evidence=None,user_log=None,user_line=None,asset_ids=None):
    pp=Path(project_path);p=load(pp);base=pp.parent
    user_ref={'log_path':user_log,'line':user_line}
    if action=='confirm-project':confirm_project(p,evidence)
    elif action=='deliver-text':
        snap=load(Path(preview)/'text_review_snapshot.json')
        if snap.get('issues'):raise ValueError('Resolve text-stage issues before recording a complete review delivery')
        mark_text_delivery(p,base,snap,evidence)
    elif action=='approve-text':approve_text(p,base,user_ref)
    elif action=='deliver-images':mark_image_delivery(p,base,gallery,evidence)
    elif action=='approve-images':approve_images(p,base,gallery,delivery_evidence or p.get('workflow',{}).get('image_delivery',{}).get('evidence'),user_ref,asset_ids)
    elif action=='approve-video':
        from execution_gate import snapshot
        receipt=load(Path(preview)/'delivery_receipt.json')
        if not receipt.get('evidence') or receipt.get('snapshot_digest')!=snapshot(preview):raise ValueError('Video preview has not been delivered or is stale')
        approve_video(p,base,snapshot(preview),user_ref,receipt)
    else:raise ValueError('Unsupported review action')
    save(pp,p);return {'action':action,'media_calls':0,'project_id':p['project_id']}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('project');ap.add_argument('action',choices=['confirm-project','deliver-text','approve-text','deliver-images','approve-images','approve-video']);ap.add_argument('--evidence');ap.add_argument('--preview');ap.add_argument('--gallery');ap.add_argument('--delivery-evidence');ap.add_argument('--user-log');ap.add_argument('--user-line',type=int);ap.add_argument('--asset-ids',nargs='+');a=ap.parse_args();print(json.dumps(record(a.project,a.action,a.evidence,a.preview,a.gallery,a.delivery_evidence,a.user_log,a.user_line,a.asset_ids),ensure_ascii=False))
