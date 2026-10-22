"""Read a specific actual user event; strings written by an agent are not approvals.
Provenance/sequence checks are not an OS security boundary against file owners.
"""
import argparse,datetime,json,re
from pathlib import Path
from core import digest

def time_value(value):
    if isinstance(value,(int,float)):return datetime.datetime.fromtimestamp(value/1000 if value>1e11 else value,datetime.timezone.utc)
    d=datetime.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if not d.tzinfo:raise ValueError('User event timestamp needs timezone')
    return d.astimezone(datetime.timezone.utc)

def content_text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '\n'.join(content_text(v) for v in value)
    if isinstance(value,dict):return str(value.get('text',''))
    return ''

def user_event(reference):
    if not isinstance(reference,dict) or not reference.get('log_path') or not reference.get('line'):
        raise ValueError('Actual user-event reference required: log_path and line, not an agent-written approval string')
    p=Path(reference['log_path']);line=int(reference['line'])
    with p.open(encoding='utf-8-sig') as f:
        row=next((json.loads(s) for n,s in enumerate(f,1) if n==line),None)
    if not row or row.get('role')!='user' or row.get('type','message')!='message':raise ValueError('Approval must come from a human user message')
    text=content_text(row.get('content',row.get('text','')))
    if any(s in text for s in ('<task-notification>','[SYSTEM NOTIFICATION - NOT USER INPUT]')):raise ValueError('Background-task event is not user approval')
    m=re.search(r'<user_query>([\s\S]*?)</user_query>',text)
    if m:text=m[1]
    elif '<system-reminder' in text:raise ValueError('No actual human query in injected context')
    timestamp=time_value(row.get('timestamp',row.get('occurred_at')))
    return {'source':{'log_path':str(p.resolve()),'line':line,'message_id':row.get('id'),'event_digest':digest(row)},'text':text.strip(),'occurred_at':timestamp.isoformat()}

def check(reference,delivered_at,stage):
    event=user_event(reference);ts=time_value(event['occurred_at'])
    if ts<=time_value(delivered_at):raise ValueError('User approval must occur after delivery of this review version')
    if ts>datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=60):raise ValueError('User approval timestamp is in the future')
    # Quoting a command or describing a later condition is not issuing that command.
    text=re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|`[^`]*`','',event['text'])
    if stage not in ('text','images','video'):raise ValueError('Unknown approval stage')
    conditional=re.search(r'(?:如果|等我|等用户|待我|待用户|待审核|等审核|等确认|待确认|确认后|看完后|审阅后|审核后)[\s\S]{0,60}(?:生成|提交|出图|出视频|启动|生图|采用|通过)',text)
    if conditional:raise ValueError('Conditional future instruction is not current stage approval')
    if stage=='video':
        preview_only=re.search(r'(?:只做|仅做|只要|做到|先做|先出|先完成)[\s\S]{0,60}(?:跑前预览|视频预览)|(?:生成视频|出视频)前[\s\S]{0,20}(?:先|要)[\s\S]{0,15}预览',text)
        denied=re.search(r'(?:不要|禁止|暂不|先不|别|不)(?:直接|自动|再|去|继续|进行|开始)?(?:生成|提交|出|跑)(?:任何|测试|正式|样片|的)?视频',text)
        denied=denied or re.search(r'(?:不需要|不用|无需|不准|不能|不可)[\s\S]{0,15}(?:生成|提交|制作|出|跑|启动|开始)[\s\S]{0,15}视频|(?:没(?:有)?|未|尚未|还没)[\s\S]{0,10}(?:授权|同意|确认|说|让|要求|看完)[\s\S]{0,25}(?:视频|预览|面板)|(?:视频|预览|面板)[^。！？\n]{0,15}(?:还没|没|未|尚未)(?:看|确认|审核|批准)',text)
        positive=re.search(r'(?:开始|启动|提交|生成|制作|出|跑)[\s\S]{0,20}(?:视频|正片|成片)|(?:start|generate|submit)[\s\S]{0,35}video',text,re.I)
        if preview_only or denied or not positive:raise ValueError('Preview-only/API-pairing request is not explicit video start approval')
    elif stage=='text':
        denied=re.search(r'(?:没(?:有)?|未|尚未|还没|不|暂不|先不|拒绝)[^。！？\n]{0,10}(?:确认|同意|看过|看完|批准|允许|审核|审阅)[^。！？\n]{0,25}(?:提示词|生图|出图|生成图片)|提示词[^。！？\n]{0,15}(?:没看|未看|不通过|没通过|未确认)|(?:不要|禁止|不准|暂不|先不|别|不需要|不用|不能)[^。！？\n]{0,15}(?:生图|出图|生成图片)',text)
        if denied:raise ValueError('User has not approved the prompt/image phase')
        if not re.search(r'(?:同意|确认|可以|开始|允许|批准|看过)[\s\S]{0,25}(?:生图|生成图片|出图|提示词)|(?:approve|accept)[\s\S]{0,30}(?:prompt|image)',text,re.I):
            raise ValueError('Need user acceptance of the delivered prompts and image phase')
    elif stage=='images':
        denied=re.search(r'(?:不通过|未通过|没通过|不确认|未确认|没确认|不采用|未采用|不可以|不要采用|不能采用)|(?:还没|尚未|未|没)[^。！？\n]{0,10}(?:看过|看完|审阅|审核)[^。！？\n]{0,15}(?:图片|图册|资产图)|(?:图片|图册|资产图)[^。！？\n]{0,10}(?:没看|未看|还没看)',text)
        if denied:raise ValueError('User has not approved the actual image gallery')
        if not re.search(r'(?:图片|图册|资产图)[\s\S]{0,20}(?:通过|确认|采用|没问题|可以)|(?:确认|采用|通过)[\s\S]{0,20}(?:图片|图册|资产图)|(?:approve|accept)[\s\S]{0,20}image',text,re.I):raise ValueError('Need user review of the delivered actual image gallery')
    return event

def verify_record(record,stage):
    source=record.get('user_event',{}).get('source',{});event=check({'log_path':source.get('log_path'),'line':source.get('line')},record['delivered_at'],stage)
    if event!=record.get('user_event'):raise ValueError('Referenced user approval event changed')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('log_path');ap.add_argument('line',type=int);a=ap.parse_args();print(json.dumps(user_event(vars(a)),ensure_ascii=False,indent=2))
