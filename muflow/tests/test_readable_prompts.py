"""Readable request regressions: original local data, no service or media calls."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from compose_requests import CASH_VISUAL_RULE, compose, compose_group


def fixture():
    assets = [
        {'id': 'adult', 'name': '周宁主状态', 'entity_id': 'parent', 'state_key': 'base'},
        {'id': 'child', 'name': '阿禾幼儿', 'entity_id': 'kid', 'state_key': 'base'},
        {'id': 'carpic', 'name': '接送车外观'},
        {'id': 'room', 'name': '门厅白天'},
        {'id': 'bagpic', 'name': '密封黑袋'},
    ]
    entities = {
        'parent': {'kind': 'person', 'identity_id': 'same-adult', 'name': '周宁'},
        'kid': {'kind': 'person', 'identity_id': 'same-child', 'name': '阿禾'},
        'driver': {'kind': 'person', 'identity_id': 'same-driver', 'name': '司机甲'},
        'car': {'kind': 'vehicle', 'name': '接送车'},
        'bag': {'kind': 'object', 'name': '密封黑袋'},
    }
    state = {'entities': {
        'parent': {'where': 'vehicle:car', 'pose': 'seated', 'seat': 'rear_right',
                   'visual_state': 'base', 'reference_asset_id': 'adult',
                   'left_hand': '密封黑袋', 'right_hand': '托住孩子身体',
                   'injury': '左臂旧伤仍在', 'clothing': '湿外套带少量血迹',
                   'stage': '产后，非孕晚期'},
        'kid': {'where': 'vehicle:car', 'pose': 'lying', 'holder': 'parent',
                'visual_state': 'base', 'reference_asset_id': 'child'},
        'driver': {'where': 'vehicle:car', 'pose': 'seated', 'seat': 'front_left'},
        'car': {'where': '门前停车处', 'motion': 'stopped'},
        'bag': {'where': 'vehicle:car', 'held_by': 'parent', 'state': '完全不透明、袋口扎紧'},
    }}
    shots = []
    for i, seconds in enumerate([10, 10, 10], 1):
        shots.append({'id': 'G01S' + str(i), 'scene_id': 'hall', 'seconds': seconds,
                      'camera': '从车外朝后排右座拍中近景。',
                      'action': '周宁保持抱持，司机甲仍在前排左座，车辆停稳。',
                      'visible_entities': ['parent', 'kid'], 'camera_subjects': ['parent'],
                      'asset_ids': ['adult', 'child', 'carpic', 'bagpic'],
                      'state_in': copy.deepcopy(state), 'state_out': copy.deepcopy(state),
                      'events': [], 'entry_bridge': [], 'speech': [], 'sound_effects': []})
    group = {'id': 'G01', 'episode': 7, 'expected_seconds': 30, 'shots': shots,
             'bindings': [{'name': a['name'], 'asset_id': a['id']} for a in assets]}
    project = {'schema_version': 2, 'settings': {'video_ratio': '9:16', 'visual_prefix': '真人写实。'},
               'entities': entities, 'assets': assets, 'groups': [group],
               'scenes': [{'id': 'hall', 'name': '门厅外停车处', 'time': '白天', 'transition': '从门厅转到停车处'}],
               'dialogues': [], 'source_path': 'PRIVATE_SOURCE_MUST_NOT_LEAK',
               'internal_path': 'PRIVATE_WORKING_DIRECTORY_MUST_NOT_LEAK'}
    return project, group


class ReadablePrompts(unittest.TestCase):
    def test_reference_order_comes_from_actual_bindings_not_asset_table(self):
        p, g = fixture()
        g['bindings'] = list(reversed(g['bindings']))
        text = compose_group(p, g)
        found = re.findall(r'@([^=\r\n@]+)=图(\d+)', text)
        self.assertEqual(found, [(b['name'], str(i)) for i, b in enumerate(g['bindings'], 1)])
        self.assertIn('外观参考：图5（周宁主状态）', text)
        self.assertIn('本镜使用参考：图5（周宁主状态）、图4（阿禾幼儿）', text)

    def test_fragments_rejoin_exact_approved_text_in_order_with_channels(self):
        p, g = fixture()
        parts = ['不是现在，', '请等我把孩子接稳。']
        for shot, part in zip(g['shots'], parts):
            shot['speech'] = [{'id': 'L7', 'speaker': '周宁', 'channel': '电话',
                               'emotion': '先克制提醒，再急切请求', 'text': part}]
        original = ''.join(parts)
        p['dialogues'] = [{'id': 'L7', 'text': original, 'speaker': '周宁', 'channel': '电话'}]
        text = compose_group(p, g)
        actual = re.findall(r'^台词 L7｜周宁｜电话原声｜先克制提醒，再急切请求：“(.*)”$', text, re.M)
        self.assertEqual(''.join(actual), original)
        self.assertEqual(actual, parts)
        self.assertEqual(text.count('台词 L7｜'), 2)

    def test_child_holder_seats_props_injury_and_stage_are_readable(self):
        p, g = fixture()
        text = compose_group(p, g)
        for expected in ['抱持者：周宁', '座位：后排右座', '司机甲——位置：接送车车内',
                         '座位：前排左座', '左手：密封黑袋', '身体伤情：左臂旧伤仍在',
                         '衣服：湿外套带少量血迹', '阶段：产后，非孕晚期',
                         '持有人：周宁', '完全不透明、袋口扎紧']:
            self.assertIn(expected, text)
        visible = [line for line in text.splitlines() if line.startswith('实际出镜：')]
        self.assertTrue(all('司机甲' not in line for line in visible))
        self.assertNotIn('驾驶席', text)  # Front-left alone does not define steering or driving.

    def test_internal_structures_and_private_paths_are_not_dumped(self):
        p, g = fixture()
        for asset in p['assets']:
            asset['local_path'] = 'PRIVATE_IMAGE_PATH'
            asset['remote_url'] = 'PRIVATE_SIGNED_URL'
        text = compose_group(p, g)
        for forbidden in ['state_in', 'state_out', 'entry_bridge', '"entities"',
                          '"before"', '"after"', '"path"', '/entities/',
                          'PRIVATE_SOURCE', 'PRIVATE_WORKING', 'PRIVATE_IMAGE', 'PRIVATE_SIGNED']:
            self.assertNotIn(forbidden, text)

    def test_decimal_time_has_no_float_tail_and_total_is_exact_thirty(self):
        p, g = fixture()
        for shot, seconds in zip(g['shots'], [0.1, 0.2, 29.7]):
            shot['seconds'] = seconds
        text = compose_group(p, g)
        self.assertIn('精确30秒', text)
        self.assertIn('镜头 G01S1｜0—0.1秒', text)
        self.assertIn('镜头 G01S2｜0.1—0.3秒', text)
        self.assertIn('镜头 G01S3｜0.3—30秒', text)
        self.assertNotIn('000000000', text)
        self.assertNotIn('30.0', text)
        g['shots'][0]['seconds'] = 0.1 + 0.2
        g['shots'][1]['seconds'] = 0.2
        g['shots'][2]['seconds'] = 29.5
        self.assertIn('精确30秒', compose_group(p, g))

    def test_unchanged_state_is_established_once_not_dumped_twice_per_shot(self):
        p, g = fixture()
        text = compose_group(p, g)
        self.assertEqual(text.count('开镜位置与状态：'), 1)
        self.assertEqual(text.count('抱持者：周宁'), 1)
        self.assertEqual(text.count('身体伤情：左臂旧伤仍在'), 1)
        self.assertNotIn('变为', text)
        self.assertEqual(text.count('镜头 G01S'), 3)

    def test_handoff_event_translates_each_important_difference(self):
        p, g = fixture()
        s = g['shots'][0]
        s['state_out']['entities']['kid']['holder'] = 'driver'
        s['events'] = [{'action': 'handoff', 'description': '周宁托稳孩子，司机甲接稳后周宁松手。',
                        'changes': [{'path': '/entities/kid/holder', 'before': 'parent', 'after': 'driver'}]}]
        for later in g['shots'][1:]:
            later['state_in'] = copy.deepcopy(s['state_out'])
            later['state_out'] = copy.deepcopy(s['state_out'])
        text = compose_group(p, g)
        self.assertIn('周宁托稳孩子，司机甲接稳后周宁松手。', text)
        self.assertIn('阿禾的抱持者由“周宁”变为“司机甲”', text)
        self.assertNotIn('镜内变化待核', text)
        self.assertNotIn('跨镜衔接待核', text)

    def test_undocumented_change_is_explicitly_pending_without_invented_action(self):
        p, g = fixture()
        g['shots'][0]['state_out']['entities']['parent']['injury'] = '未明'
        text = compose_group(p, g)
        self.assertIn('镜内变化待核（动作依据未登记，不能用切镜跳变）', text)
        self.assertIn('周宁的身体伤情由“左臂旧伤仍在”变为“未明”', text)
        self.assertNotIn('伤口愈合', text)

    def test_boarding_door_and_walk_route_survive_event_rendering(self):
        p, g = fixture()
        g['shots'][0]['events'] = [
            {'action': 'board', 'description': '周宁从右后门入座。', 'entity_id': 'parent',
             'vehicle_id': 'car', 'seat': 'rear_right', 'door': 'right_rear', 'changes': []},
            {'action': 'walk_around', 'description': '停稳后沿车后绕行。', 'entity_id': 'driver',
             'vehicle_id': 'car', 'route': '车后', 'changes': []}]
        text = compose_group(p, g)
        for expected in ['执行者：周宁', '载具：接送车', '座次：后排右座', '车门：右后门', '绕行路径：车后']:
            self.assertIn(expected, text)

    def test_actual_scene_time_transition_and_camera_are_preserved(self):
        p, g = fixture()
        p['scenes'].append({'id': 'night', 'name': '门厅内', 'time': '夜晚', 'transition': '次日晚归，明确转场'})
        g['shots'][2]['scene_id'] = 'night'
        text = compose_group(p, g)
        self.assertIn('转场至：门厅内（night）；时间：夜晚；转场／承前要求：次日晚归，明确转场', text)
        self.assertIn('机位与构图：从车外朝后排右座拍中近景。', text)

    def test_physical_audio_and_locked_rules_remain_explicit(self):
        p, g = fixture()
        g['shots'][0]['sound_effects'] = [{'physical_source': '车门锁扣', 'purpose': '确认停车后的车门关妥',
                                         'trigger': '右后门合上时'}]
        text = compose_group(p, g)
        self.assertIn(CASH_VISUAL_RULE, text)
        self.assertIn('画面禁止文字与字幕', text)
        self.assertIn('禁止背景音乐与环境氛围音', text)
        self.assertIn('必要物理音效：车门锁扣；用途：确认停车后的车门关妥', text)
        self.assertIn('右后门合上时', text)

    def test_unknown_state_is_retained_and_pending_not_given_a_guessed_position(self):
        p, g = fixture()
        for shot in g['shots']:
            for state in (shot['state_in'], shot['state_out']):
                state['entities']['kid']['where'] = 'opaque:anchor_17'
                state['entities']['kid']['custom_stage'] = {'continuity_flag': 'unmapped_value'}
        text = compose_group(p, g)
        self.assertIn('世界锚点“opaque:anchor_17”（空间含义待核，不自行指定左右或距离）', text)
        self.assertIn('未定义状态项“custom_stage”（含义待核）', text)
        self.assertIn('unmapped_value（含义待核）', text)
        self.assertNotIn('anchor_17左侧', text)

    def test_previous_group_end_is_checked_without_replaying_it(self):
        p, g = fixture()
        previous = copy.deepcopy(g['shots'][0])
        previous['id'] = 'BEFORE'
        previous['state_out']['entities']['parent']['right_hand'] = '手机'
        p['groups'].insert(0, {'id': 'G00', 'shots': [previous]})
        text = compose_group(p, g)
        self.assertIn('跨镜衔接待核', text)
        self.assertIn('周宁的右手由“手机”变为“托住孩子身体”', text)
        self.assertNotIn('镜头 BEFORE', text)

    def test_registered_visual_stage_codes_render_using_the_correct_person_asset(self):
        p, g = fixture()
        p['assets'].append({'id': 'adult-wet', 'name': '周宁雨衣阶段',
                            'entity_id': 'parent', 'state_key': 'rain', 'parent_id': 'adult'})
        g['bindings'].append({'name': '周宁雨衣阶段', 'asset_id': 'adult-wet'})
        s = g['shots'][0]
        s['state_out']['entities']['parent']['visual_state'] = 'rain'
        s['state_out']['entities']['parent']['reference_asset_id'] = 'adult-wet'
        s['events'] = [{'action': 'change_costume', 'description': '按已批准转场换上雨衣。', 'changes': [
            {'path': '/entities/parent/visual_state', 'before': 'base', 'after': 'rain'},
            {'path': '/entities/parent/reference_asset_id', 'before': 'adult', 'after': 'adult-wet'}]}]
        for later in g['shots'][1:]:
            later['state_in'] = copy.deepcopy(s['state_out'])
            later['state_out'] = copy.deepcopy(s['state_out'])
        text = compose_group(p, g)
        self.assertIn('周宁的外观阶段由“周宁主状态所示状态”变为“周宁雨衣阶段所示状态”', text)
        self.assertIn('图6（周宁雨衣阶段）', text)
        self.assertNotIn('rain（含义待核）', text)
        self.assertNotIn('未定义状态项', text)

    def test_compile_writes_identical_text_without_mutating_story_or_overwriting(self):
        p, g = fixture()
        original = copy.deepcopy(p)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project_file = base / 'project.json'
            project_file.write_text(json.dumps(p, ensure_ascii=False), encoding='utf-8')
            result = compose(project_file, base / 'requests-v1')
            self.assertEqual(result['mode'], 'local_compile_only')
            actual = (base / 'requests-v1' / 'G01.txt').read_text(encoding='utf-8')
            self.assertEqual(actual, compose_group(p, g))
            loaded = json.loads(project_file.read_text(encoding='utf-8'))
            loaded['groups'][0].pop('prompt_path')
            self.assertEqual(loaded, original)
            with self.assertRaisesRegex(ValueError, 'new request-version'):
                compose(project_file, base / 'requests-v1')
        self.assertEqual(p, original)

    def test_invalid_duration_remains_diagnosable_not_falsely_thirty(self):
        p, g = fixture()
        g['expected_seconds'] = 15
        text = compose_group(p, g)
        self.assertIn('时长待核：登记15秒，逐镜合计30秒', text)
        self.assertNotIn('；精确30秒；', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
