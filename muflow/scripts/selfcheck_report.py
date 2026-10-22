"""Combine recorded checks/issues/feedback; never fabricate root causes or passes."""
import argparse,json
from collections import Counter
from pathlib import Path
from core import load

def project_records(p):
    from review_state import mode,visual_error,semantic_error,require_human_asset
    checks=[];issues=[]
    from asset_gallery import required_ids
    needed=required_ids(p)
    current_assets=[a for a in p.get('assets',[]) if a['id'] in needed]
    try:selected=mode(p)
    except ValueError:
        selected=None;checks.append({'id':'资产验收方式','object':'settings','version':p.get('version'),'rule_id':'asset-review-mode','status':'待复核','evidence':'尚未选择验收方式，图片审核等待安排'})
    objects=[('分镜语义审核',p.get('groups',[]),semantic_error)]
    if selected in ('agent','joint'):objects.insert(0,('实际图片AI审核',current_assets,visual_error))
    for kind,items,validator in objects:
        for item in items:
            review=item.get('review',{});error=validator(p,item)
            checks.append({'id':kind,'object':item['id'],'version':item.get('version',p.get('version')),
              'rule_id':kind,'status':'待复核' if error else '通过','executed_at':review.get('checked_at'),
              'execution_status':'已记录实际审核' if not error else '记录不完整或内容已变',
              'evidence':review.get('checks') or error,'basis':review.get('evidence'),'performed_by':review.get('performed_by')})
    for item in current_assets:
        if selected in ('manual','joint'):
            try:record=require_human_asset(p,item);error=None
            except (ValueError,KeyError) as exc:record={};error=str(exc)
            checks.append({'id':'人工图片采用核验','object':item['id'],'version':item.get('version',p.get('version')),
                'rule_id':'actual-human-adoption','status':'待复核' if error else '通过',
                'executed_at':record.get('recorded_at'),'execution_status':'程序核验实际用户采用记录；不替用户评价图片',
                'evidence':error or record.get('user_event',{}).get('source')})
        for issue in item.get('review',{}).get('issues',[]):issues.append(dict(issue,object=item['id'],version=item.get('version',p.get('version'))))
    for row in p.get('workflow',{}).get('asset_feedback',[]):issues.append(dict(row,category='用户反馈',object=row.get('asset_id',row.get('asset_ids')),version=p.get('version')))
    return checks,issues

def build(inputs):
    checks=[];issues=[];task_rows=[];mode_notes=[]
    for file in inputs:
        d=load(file)
        if isinstance(d,list):issues.extend(d);continue
        if d.get('schema_version')==2 and 'assets' in d:
            mode_notes.append('资产验收模式：'+str(d.get('settings',{}).get('asset_review_mode','joint'))+'；未选择的审阅环节不适用，不计入待复核数量。')
            rows,findings=project_records(d);checks.extend(rows);issues.extend(findings)
        checks.extend(d.get('checks',[]));issues.extend(d.get('issues',[]));task_rows.extend(d.get('tasks',[]))
    normalized=[]
    for c in checks:
        c=dict(c)
        if c.get('status')=='通过' and (not c.get('executed_at') or not c.get('evidence') or not c.get('rule_id')):
            c.update(status='待复核',record_warning='缺少执行时间/规则/证据；不能汇总为已通过')
        if c.get('status') not in ('通过','不通过','待复核'):c.update(status='待复核',execution_status=c.get('status','未执行'))
        normalized.append(c)
    checks=normalized
    counts=Counter(x['status'] for x in checks)
    out=['# 自检报告汇总','', '范围：仅汇总输入记录。程序检查、语义回对、实际图片审核及用户反馈按原证据分别列示。', '', '## 检查记录','']
    out+=mode_notes+['']
    out+=['；'.join(f'{k}：{v}' for k,v in counts.items()) or '未提供检查记录，不推定通过。','']
    for c in checks:out += [f'- {c.get("id","")}｜{c.get("object","")}｜{c.get("version","")}｜{c.get("status","未执行")}：{c.get("evidence","依据未填写")}；执行：{c.get("execution_status","待核实")}；{c.get("record_warning","")}']
    categories=Counter(i.get('category','待分类') for i in issues)
    out+=['','## 问题、成因和下次处理','', '记录数：'+str(len(issues))+'；对象版本去重后：'+str(len({(i.get('id'),json.dumps(i.get('object'),ensure_ascii=False,sort_keys=True),i.get('version')) for i in issues}))+'；分类：'+str(dict(categories)), '']
    for i in issues:
        out += [f'### {i.get("id","")} · {i.get("object","")}',
                f'- 分类/状态：{i.get("category","待分类")} / {i.get("status","待复核")}',
                f'- 对象版本/依据：{i.get("version","未记录")} / {i.get("rule_id","未记录")}',f'- 预期：{i.get("expected","未记录")}',f'- 实际：{i.get("actual","未记录")}',
                f'- 证据与确定性：{i.get("evidence","未记录")} / {i.get("certainty","待验证")}',
                f'- 直接原因：{i.get("direct_cause","待核对")}',f'- 流程根因：{i.get("root_cause","待验证")}',
                f'- 修复：{i.get("fix","未执行")}',f'- 下次处理：{i.get("next_action",i.get("prevention","待制定"))}',
                f'- 复检与影响：{i.get("recheck","未执行")} / {i.get("affected",[])}','']
    out+=['## 制作任务','']
    for t in task_rows:out.append('- '+json.dumps(t,ensure_ascii=False))
    return '\n'.join(out)+'\n'
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',nargs='+');p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out)
    if out.exists():p.error('Report exists; choose a new version')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(build(a.inputs),encoding='utf-8');print(out)
