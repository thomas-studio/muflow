"""Dialogue perspectives and prop inserts use declared story state; offline only."""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_readable_prompts import fixture
from continuity import audit
from compose_requests import compose_group

class ShotFocus(unittest.TestCase):
    def build(self):
        p,g=fixture();shots=g['shots']
        for a in p['assets']:
            if a['id']=='bagpic':a.update(entity_id='bag',state_key='base')
        for shot in shots:
            for state in (shot['state_in'],shot['state_out']):state['entities']['bag'].update(visual_state='base',reference_asset_id='bagpic')
        shots[0]['speech']=[{'id':'D1','speaker':'周宁','channel':'现场','emotion':'平静','text':'我们把','camera_relation':'onscreen'}]
        shots[1].update(visible_entities=['bag'],camera_subjects=['bag'],camera='黑袋特写',action='黑袋保持封口。')
        shots[1]['speech']=[{'id':'D1','speaker':'周宁','channel':'现场','emotion':'平静','text':'东西带走。','camera_relation':'offscreen','continues_from_previous':True}]
        return p,g
    def test_prop_view_keeps_original_voice_and_words(self):
        p,g=self.build();txt=compose_group(p,g)
        self.assertIn('画面说明：本镜无需人物入画',txt)
        self.assertIn('周宁的现场对白从画外传来',txt)
        self.assertIn('这是周宁同一句台词的后续片段',txt)
        self.assertIn('本镜使用参考：',txt)
        relevant=[e for e in audit(p,[g]) if e[0] in ('CAMERA_SUBJECT','CAMERA_REFERENCE','CAMERA_RELATION','SPEECH_CONTINUATION')]
        self.assertEqual(relevant,[])
    def test_visible_listener_does_not_change_speaker(self):
        p,g=self.build();shot=g['shots'][1];shot['visible_entities']=['kid'];shot['camera_subjects']=['kid']
        txt=compose_group(p,g)
        self.assertIn('实际出镜：阿禾；镜头主体：阿禾',txt)
        self.assertIn('台词 D1｜周宁｜现场对白｜平静：“东西带走。”',txt)
        self.assertIn('画中人物不代替发声或对口型',txt)
    def test_prop_reference_must_be_bound_when_registered(self):
        p,g=self.build();g['shots'][1]['asset_ids'].remove('bagpic')
        self.assertTrue(any(e[0]=='CAMERA_REFERENCE' for e in audit(p,[g])))
    def test_offscreen_speaker_is_not_visible(self):
        p,g=self.build();g['shots'][1]['visible_entities'].append('parent')
        self.assertTrue(any(e[0]=='CAMERA_RELATION' for e in audit(p,[g])))
    def test_continuation_requires_same_dialogue_in_previous_shot(self):
        p,g=self.build();g['shots'][1]['speech'][0]['id']='D2'
        self.assertTrue(any(e[0]=='SPEECH_CONTINUATION' for e in audit(p,[g])))
    def test_phone_voice_keeps_phone_channel(self):
        p,g=self.build();g['shots'][1]['speech'][0]['channel']='电话'
        txt=compose_group(p,g);self.assertIn('周宁的电话原声从画外传来',txt)

    def test_onscreen_speaker_must_be_visible_when_identity_is_known(self):
        p,g=self.build();g['shots'][1]['speech'][0]['camera_relation']='onscreen'
        self.assertTrue(any(e[0]=='CAMERA_RELATION' for e in audit(p,[g])))
    def test_explicit_tail_edit_is_not_a_shorter_request(self):
        p,g=self.build();g['trim_tail_seconds']=3;g['trim_tail_basis']='末尾保留镜尾剪辑余量'
        text=compose_group(p,g)
        self.assertIn('精确30秒',text);self.assertIn('建议保留前27秒',text);self.assertIn('尾部3秒可人工裁剪',text)
        self.assertIn('不靠延长对白或重复动作填充',text)

    def test_preview_shows_dialogue_relation_and_tail(self):
        from build_preview import _group_card
        p,g=self.build();g['trim_tail_seconds']=3;g['trim_tail_basis']='人工保留前27秒'
        card=_group_card(p,g,7,{a['id']:a for a in p['assets']},{},None,None,None)
        self.assertIn('说话人画外',card);self.assertIn('同句接上镜',card)
        self.assertIn('建议留用前 27 秒',card);self.assertIn('尾部 3 秒可人工剪辑',card)
        self.assertIn('本集镜头分配',card)

    def test_timing_warning_is_reported_as_pending_not_passed(self):
        from selfcheck_report import project_records
        p,g=self.build();p['settings'].update(timing_mode='company-speech-ratio',group_target_seconds=30,speech_chars_per_second=5,non_speech_ratio_min=0.1,non_speech_ratio_max=0.15,non_speech_ratio_target=0.1)
        p['episodes']=[{'id':7}];p['dialogues']=[{'id':'D1','episode':7,'text':'我们把东西带走。'}]
        for shot in g['shots']:shot['timing']={'action_seconds':1,'action_basis':'有依据的合成动作','allocation_basis':'本测试余量待复核'}
        checks,_=project_records(p)
        flagged=[x for x in checks if x.get('id')=='计时与镜头节奏提示']
        self.assertTrue(flagged);self.assertTrue(all(x['status']=='待复核' for x in flagged))

if __name__=='__main__':unittest.main()
