"""Synthetic human event fixtures; never production approval evidence."""
import datetime,json,uuid
from pathlib import Path

def event(base,text,timestamp=None,role='user',kind='message'):
    p=Path(base)/'synthetic-user-events.jsonl'
    row={'id':'synthetic-'+uuid.uuid4().hex,'type':kind,'role':role,'timestamp':timestamp or (datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(milliseconds=2)).isoformat(),'content':[{'type':'text','text':text}]}
    old=p.read_text(encoding='utf-8') if p.exists() else ''
    line=len(old.splitlines())+1;p.write_text(old+json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8')
    return {'log_path':str(p),'line':line}
