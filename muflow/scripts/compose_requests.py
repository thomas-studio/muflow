"""Compile internal continuity data into readable, exact video directions."""
import argparse
import json
import re
from decimal import Decimal
from pathlib import Path
from core import load, save

CASH_VISUAL_RULE = '若剧情涉及现金：现金只置于完全封闭、不透明的包装道具内，全程不露纸币、硬币、钞票边缘、捆扎带或内容物。即使原文写撒钱、倒钱、现金散落或满天飞，也禁止表现；执行为封闭包装的交付、搬动或仍密封的落地，金额和关键结果保持已批准版本。不打开内包装、不倾倒、不撕裂、不数裸钞、不拍捡裸钱，不用马赛克或模糊补救。'

_FIELD_NAMES = {
    'where': '位置', 'pose': '姿态', 'visual_state': '外观阶段',
    'reference_asset_id': '外观参考', 'holder': '抱持者', 'held_by': '持有人',
    'seat': '座位', 'left_hand': '左手', 'right_hand': '右手',
    'holding': '持物', 'handheld': '手持物', 'props': '道具',
    'injury': '身体伤情', 'wound': '伤口', 'clothing': '衣服',
    'costume': '服装', 'blood': '血迹', 'wetness': '湿润状态',
    'pregnancy': '孕期', 'stage': '阶段', 'age': '年龄',
    'driver': '驾驶者', 'vehicle_id': '所属载具', 'door': '车门',
    'doors': '车门状态', 'engine': '发动机', 'motion': '运动状态',
    'orientation': '朝向', 'facing': '面向', 'position': '位置',
    'steering': '方向盘位置', 'speed': '速度', 'hand': '持物手',
    'visible': '可见状态', 'location': '位置', 'state': '状态',
    'trigger': '触发时点', 'description': '具体表现', 'condition': '触发条件',
    'start_second': '开始秒点', 'end_second': '结束秒点',
    'duration_seconds': '持续秒数', 'at_second': '触发秒点',
}
_SEATS = {'front_left': '前排左座', 'front_right': '前排右座',
          'rear_left': '后排左座', 'rear_right': '后排右座',
          'rear_middle': '后排中座', 'rear_center': '后排中座'}
_DOORS = {'left_front': '左前门', 'right_front': '右前门',
          'left_rear': '左后门', 'right_rear': '右后门'}
_VALUES = {'standing': '站立', 'seated': '坐姿', 'sitting': '坐姿',
           'lying': '躺卧', 'walking': '行走', 'running': '奔跑',
           'open': '打开', 'closed': '关闭', 'stopped': '停稳',
           'moving': '行驶中', 'parked': '停放', 'base': '主状态',
           'left': '左侧', 'right': '右侧', 'none': '无',
           'unknown': '未明（待核，不自行补设）',
           'offscreen': '画外（继续保留位置与持有关系）'}
_CHANNELS = {'现场': '现场对白', '现场对白': '现场对白',
             '电话': '电话原声', 'phone': '电话原声',
             '内心': '内心独白', 'inner': '内心独白',
             '消息': '手机消息原声', '手机消息': '手机消息原声',
             'message': '手机消息原声', '画外': '画外原声'}
_MISSING = object()


def _decimal(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Shot/group seconds must be finite')
    # JSON floats may have a binary arithmetic tail; retain nanosecond precision.
    if isinstance(value, float):
        number = number.quantize(Decimal('0.000000001'))
    return number


def _seconds(value):
    text = format(_decimal(value), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


class _Readable:
    def __init__(self, project, group):
        self.entities = project.get('entities', {})
        self.assets = {a['id']: a for a in project.get('assets', [])}
        self.scenes = {s['id']: s for s in project.get('scenes', [])}
        self.bound = {b['asset_id']: (i, b['name'])
                      for i, b in enumerate(group.get('bindings', []), 1)}

    def name(self, eid):
        if eid is None:
            return '未登记（待核）'
        entity = self.entities.get(eid, {})
        if entity.get('name'):
            return str(entity['name'])
        asset = next((a for a in self.assets.values()
                      if a.get('entity_id') == eid and not a.get('parent_id')), None)
        if asset and asset.get('name'):
            return str(asset['name'])
        if eid in self.assets:
            return str(self.assets[eid].get('name') or eid)
        return str(eid) + '（名称未登记，待核）'

    def reference(self, aid):
        if aid in self.bound:
            number, name = self.bound[aid]
            return '图' + str(number) + '（' + name + '）'
        return str(self.assets.get(aid, {}).get('name') or aid) + '（本组未绑定，待核）'

    def label(self, key):
        if key in _FIELD_NAMES:
            return _FIELD_NAMES[key]
        if key in _SEATS:
            return _SEATS[key]
        if key in _DOORS:
            return _DOORS[key]
        if re.search(r'[\u3400-\u9fff]', str(key)):
            return str(key)
        return '未定义状态项“' + str(key) + '”（含义待核）'

    def value(self, value, key='', entity_id=None):
        if value is _MISSING:
            return '未登记（待核，不据此让人物或道具消失）'
        if value is None:
            if key in ('holder', 'held_by'):
                return '无人物抱持／持有，去向按位置与座次执行'
            if key == 'seat':
                return '不独占座位，位置与抱持关系按登记执行'
            return '未明（待核，不自行补设）'
        if isinstance(value, bool):
            return '是' if value else '否'
        if isinstance(value, (int, float)):
            return _seconds(value)
        if isinstance(value, dict):
            if not value:
                return '未登记明细（待核）'
            return '；'.join(self.label(k) + '为' + self.value(v, k, entity_id) for k, v in value.items())
        if isinstance(value, list):
            return '、'.join(self.value(v, key, entity_id) for v in value) if value else '无已登记项目'
        text = str(value)
        if key == 'visual_state' and entity_id is not None:
            asset = next((a for a in self.assets.values()
                          if a.get('entity_id') == entity_id and a.get('state_key') == text), None)
            if asset and asset.get('name'):
                return str(asset['name']) + '所示状态'
        if key == 'reference_asset_id':
            return self.reference(text)
        if text in self.entities or key in ('holder', 'held_by', 'driver', 'vehicle_id'):
            return self.name(text)
        if text in self.assets:
            return self.reference(text) if text in self.bound else self.assets[text].get('name', text)
        if key in ('where', 'location'):
            if text.startswith('vehicle:'):
                return self.name(text.split(':', 1)[1]) + '车内'
            if text in self.scenes:
                return str(self.scenes[text].get('name') or text)
            if re.search(r'[\u3400-\u9fff]', text) and ':' not in text:
                return text
            return '世界锚点“' + text + '”（空间含义待核，不自行指定左右或距离）'
        if key == 'seat':
            return _SEATS.get(text, text + '（座位定义待核）')
        if key == 'door':
            return _DOORS.get(text, text + '（车门定义待核）')
        if text in _VALUES:
            return _VALUES[text]
        if re.search(r'[\u3400-\u9fff]', text):
            return text
        return text + '（含义待核）'

    def subject(self, parts):
        if parts and parts[0] == 'entities':
            if len(parts) == 1:
                return '世界人物／物件账本', ''
            result = self.name(parts[1])
            fields = parts[2:]
        else:
            result = '场面'
            fields = parts
        if fields:
            result += '的' + '／'.join(self.label(k) for k in fields)
        return result, fields[-1] if fields else ''

    def change(self, change):
        parts = [p.replace('~1', '/').replace('~0', '~')
                 for p in str(change.get('path', '')).split('/') if p]
        subject, key = self.subject(parts)
        entity_id = parts[1] if len(parts) > 1 and parts[0] == 'entities' else None
        return (subject + '由“' + self.value(change.get('before', _MISSING), key, entity_id)
                + '”变为“' + self.value(change.get('after', _MISSING), key, entity_id) + '”')

    def state(self, state):
        if not isinstance(state, dict) or not state:
            return '状态未登记，待核后执行，不补造人物位置或持物'
        rows = []
        for key, value in state.items():
            if key == 'entities' and isinstance(value, dict):
                for eid, details in value.items():
                    if isinstance(details, dict):
                        facts = [self.label(k) + '：' + self.value(v, k, eid) for k, v in details.items()]
                        rows.append(self.name(eid) + '——' + '；'.join(facts))
                    else:
                        rows.append(self.name(eid) + '——' + self.value(details))
            else:
                rows.append(self.label(key) + '：' + self.value(value, key))
        return '。'.join(rows)

    def event(self, event):
        description = event.get('description') or '动作说明缺失（待核）'
        details = []
        if event.get('entity_id'):
            details.append('执行者：' + self.name(event['entity_id']))
        if event.get('vehicle_id'):
            details.append('载具：' + self.name(event['vehicle_id']))
        if event.get('seat'):
            details.append('座次：' + self.value(event['seat'], 'seat'))
        if event.get('door'):
            details.append('车门：' + self.value(event['door'], 'door'))
        if event.get('route'):
            details.append('绕行路径：' + self.value(event['route']))
        # Keep extension fields readable; never silently discard a production constraint.
        for key, value in event.items():
            if key not in ('description', 'action', 'changes', 'entity_id', 'vehicle_id',
                           'seat', 'door', 'route', 'source_evidence'):
                details.append(self.label(key) + '：' + self.value(value, key))
        changes = [self.change(c) for c in event.get('changes', [])]
        return str(description) + ('；' + '；'.join(details + changes) if details or changes else '')


def _leaves(value, parts=()):
    if isinstance(value, dict) and value:
        return {p: v for key, item in value.items() for p, v in _leaves(item, parts + (key,)).items()}
    return {parts: value}


def _unexplained(before, after, events):
    old, new = _leaves(before), _leaves(after)
    covered = {tuple(p.replace('~1', '/').replace('~0', '~')
                     for p in str(c.get('path', '')).split('/') if p)
               for e in events for c in e.get('changes', [])}
    result = []
    for parts in sorted(set(old) | set(new)):
        if old.get(parts, _MISSING) != new.get(parts, _MISSING) and parts not in covered:
            result.append({'path': '/' + '/'.join(p.replace('~', '~0').replace('/', '~1') for p in parts),
                           'before': old.get(parts, _MISSING), 'after': new.get(parts, _MISSING)})
    return result


def compose_group(project, g):
    settings = project.get('settings', {})
    readable = _Readable(project, g)
    shots = g.get('shots', [])
    duration = sum((_decimal(s['seconds']) for s in shots), Decimal(0))
    expected = _decimal(g['expected_seconds'])
    duration_note = '精确30秒' if duration == expected == 30 else (
        '时长待核：登记' + _seconds(expected) + '秒，逐镜合计' + _seconds(duration) + '秒；须修正为精确30秒再执行')
    lines = ['第' + str(g['episode']) + '集 · 生成组 ' + g['id'] + '；' + duration_note
             + '；画幅 ' + str(settings.get('video_ratio') or '未确认（待核）'),
             '画面禁止文字与字幕。声音仅角色对白和必要物理音效，禁止背景音乐与环境氛围音。',
             '逐字执行台词；内心、电话及消息按已标声道，不改为现场发言。参考图中多角度展示属于同一身份，实际出镜人数按逐镜名单，不复制人物。',
             CASH_VISUAL_RULE]
    for text in (settings.get('visual_prefix'), g.get('directing_notes')):
        if text:
            lines.append(str(text))
    bindings = g.get('bindings', [])
    if bindings:
        lines.append('参考图顺序（与本次实际传图顺序一致）：' + ' / '.join(
            '@' + b['name'] + '=图' + str(i) for i, b in enumerate(bindings, 1)))
    else:
        lines.append('参考图未绑定（待核）。')
    lines.append('车辆左右与座位均以车辆自身朝前为准，不能按画面左右换位；未登记的司机、座位、伤因和时刻不自行补设。')
    elapsed = Decimal(0)
    previous = None
    previous_scene = None
    # A standalone group must also state the actual preceding group's end-state differences.
    all_shots = [s for group in project.get('groups', []) for s in group.get('shots', [])]
    if shots:
        index = next((i for i, s in enumerate(all_shots) if s.get('id') == shots[0].get('id')), None)
        if index is not None and index > 0:
            previous = all_shots[index - 1]
            previous_scene = previous.get('scene_id')
    for shot_index, shot in enumerate(shots):
        end = elapsed + _decimal(shot['seconds'])
        scene = readable.scenes.get(shot.get('scene_id'), {})
        name = str(scene.get('name') or '名称未登记（待核）')
        time = str(scene.get('time') or '时间未明（不擅定日夜）')
        lines.append('')
        lines.append('镜头 ' + shot['id'] + '｜' + _seconds(elapsed) + '—' + _seconds(end) + '秒')
        scene_prefix = '场景' if shot_index == 0 and previous_scene is None else (
            '承前场景' if previous_scene == shot.get('scene_id') else '转场至')
        scene_text = scene_prefix + '：' + name + '（' + str(shot.get('scene_id', '未编号')) + '）；时间：' + time
        if scene.get('transition') and (shot_index == 0 or previous_scene != shot.get('scene_id')):
            scene_text += '；转场／承前要求：' + str(scene['transition'])
        lines.append(scene_text)
        bridges = shot.get('entry_bridge', [])
        for bridge in bridges:
            lines.append('入镜承接：' + readable.event(bridge))
        if previous is not None:
            missing = _unexplained(previous.get('state_out', {}), shot.get('state_in', {}), bridges)
            if missing:
                lines.append('跨镜衔接待核（缺少已登记的动作或转场依据）：' + '；'.join(readable.change(c) for c in missing))
        if shot_index == 0:
            lines.append('开镜位置与状态：' + readable.state(shot.get('state_in', {})))
        if shot.get('entry_state'):
            lines.append('入镜调度要求：' + str(shot['entry_state']))
        visible = shot.get('visible_entities', [])
        subjects = shot.get('camera_subjects', [])
        lines.append('实际出镜：' + ('、'.join(readable.name(e) for e in visible) if visible else '无已登记人物，是否空镜待核')
                     + '；镜头主体：' + ('、'.join(readable.name(e) for e in subjects) if subjects else '未登记（待核）'))
        refs = [readable.reference(aid) for aid in shot.get('asset_ids', [])]
        if refs:
            lines.append('本镜使用参考：' + '、'.join(refs))
        lines.append('机位与构图：' + str(shot.get('camera') or '未登记（待核）'))
        lines.append('动作与表演：' + str(shot.get('action') or '未登记（待核）'))
        events = shot.get('events', [])
        for event in events:
            lines.append('动作连续性：' + readable.event(event))
        missing = _unexplained(shot.get('state_in', {}), shot.get('state_out', {}), events)
        if missing:
            lines.append('镜内变化待核（动作依据未登记，不能用切镜跳变）：' + '；'.join(readable.change(c) for c in missing))
        for fragment in shot.get('speech', []):
            speaker = fragment.get('speaker')
            speaker = readable.name(speaker) if speaker in readable.entities else str(speaker or '说话人未登记（待核）')
            channel = fragment.get('channel') or '声道未登记（待核）'
            lines.append('台词 ' + str(fragment.get('id', '未编号')) + '｜' + speaker
                         + '｜' + _CHANNELS.get(channel, channel) + '｜'
                         + str(fragment.get('emotion') or '语气未登记（待核）') + '：“'
                         + str(fragment.get('text', '')) + '”')
        for effect in shot.get('sound_effects', []):
            if isinstance(effect, dict):
                source = effect.get('physical_source') or '实际声源未登记（待核）'
                details = ['用途：' + str(effect.get('purpose') or '必要性未登记（待核）')]
                for key, value in effect.items():
                    if key not in ('physical_source', 'purpose'):
                        details.append(readable.label(key) + '：' + readable.value(value, key))
                lines.append('必要物理音效：' + str(source) + '；' + '；'.join(details))
            else:
                lines.append('必要物理音效：' + str(effect) + '（实际声源与必要性待核）')
        if shot.get('exit_state'):
            lines.append('收镜调度要求：' + str(shot['exit_state']))
        if not events and not missing and not shot.get('exit_state'):
            lines.append('收镜承接：保持已建立的位置、座次、抱持、持物和身体衣着状态；下一镜不重演已完成动作。')
        elapsed, previous, previous_scene = end, shot, shot.get('scene_id')
    return '\n'.join(lines) + '\n'


def compose(project_path, out):
    p = Path(project_path)
    data = load(p)
    target = Path(out)
    if target.exists():
        raise ValueError('Use a new request-version directory')
    target.mkdir(parents=True)
    for group in data.get('groups', []):
        file = target / (group['id'] + '.txt')
        file.write_text(compose_group(data, group), encoding='utf-8', newline='\n')
        group['prompt_path'] = str(file.resolve())
    save(p, data)
    return {'groups': len(data.get('groups', [])), 'project': str(p), 'mode': 'local_compile_only'}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('out')
    args = ap.parse_args()
    print(json.dumps(compose(args.project, args.out), ensure_ascii=False))
