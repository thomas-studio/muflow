"""Company 5 characters/s timing and speech-to-nonspeech budget regressions."""
import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from timing_plan import *
from validate_project import validate
from core import save

class CompanyTiming(unittest.TestCase):
    def project(self):return {'schema_version':2,'settings':{'timing_mode':'company-speech-ratio','speech_chars_per_second':5,'non_speech_ratio_min':.1,'non_speech_ratio_max':.15,'non_speech_ratio_target':.1},'episodes':[{'id':1}], 'dialogues':[],'groups':[]}
    def shot(self,id,text,action=0):return {'id':id,'seconds':30,'speech':[{'id':id,'text':text,'speaker':'甲','channel':'现场'}] if text else [],'timing':{'action_seconds':action,'action_basis':'合成的必要动作','allocation_basis':'已登记的剪辑余量'}}
    def group(self,id,shot,**kw):return {'id':id,'episode':1,'expected_seconds':30,'shots':[shot]}|kw
    def test_initial_250_includes_os_once(self):
        p=self.project();p['dialogues']=[{'id':'D1','episode':1,'text':'甲'*100,'channel':'现场'},{'id':'D2','episode':1,'text':'乙'*150,'channel':'内心'}]
        x=build_plan(p)['episode_dialogue_budgets'][0]
        self.assertEqual(x['spoken_characters'],250);self.assertEqual(x['speech_seconds_estimate'],50)
        self.assertEqual((x['non_speech_budget_min'],x['non_speech_budget_max']),(5,7.5))
        self.assertEqual(x['minimum_groups_by_budget'],2);self.assertEqual(x['budget_generated_seconds'],60);self.assertEqual(x['budget_headroom_seconds'],5)
    def test_budget_boundary_ten_vs_fifteen_percent(self):
        p=self.project();p['dialogues']=[{'id':'D','episode':1,'text':'甲'*265,'channel':'内心'}]
        self.assertEqual(build_plan(p)['episode_dialogue_budgets'][0]['minimum_groups_by_budget'],2)
        p['settings']['non_speech_ratio_target']=.15
        self.assertEqual(build_plan(p)['episode_dialogue_budgets'][0]['minimum_groups_by_budget'],3)
    def test_groups_real_action_and_explicit_trim(self):
        p=self.project();a=self.shot('A','甲'*125,5);b=self.shot('B','乙'*125,0)
        p['groups']=[self.group('G1',a),self.group('G2',b,trim_tail_seconds=5,trim_tail_basis='末镜5秒供人工剪裁')]
        report=build_plan(p);self.assertEqual(report['issues'],[]);x=report['episodes'][0]
        self.assertEqual(x['spoken_characters'],250);self.assertEqual(x['speech_seconds_estimate'],50);self.assertEqual(x['non_speech_actual_seconds_estimate'],5)
        self.assertEqual(x['ratio_status'],'目标内');self.assertEqual(x['planning_estimate_seconds'],55);self.assertEqual(x['minimum_groups_by_estimate'],2)
        self.assertEqual(x['generated_seconds'],60);self.assertEqual(x['trim_tail_seconds'],5)
        self.assertIsNone(report['groups'][0]['effective_story_seconds']);self.assertEqual(report['groups'][1]['effective_story_seconds'],25)
    def test_speech_including_inner_os_in_reaction_counts_as_voice(self):
        p=self.project();s=self.shot('A','甲'*50,2);s['speech'][0]['channel']='内心';s['timing'].update(overlap_seconds=2,overlap_evidence='反应动作在同一OS声音期间进行')
        r=estimate_shot(s,timing_settings(p));self.assertEqual(r['speech_seconds_estimate'],10);self.assertEqual(r['non_speech_seconds_estimate'],0)
    def test_no_automatic_action_beat_22(self):
        p=self.project();s=self.shot('A','甲'*10);s['timing']={'action_beats':1,'action_basis':'转身'}
        with self.assertRaisesRegex(TimingError,'实际动作秒数'):estimate_shot(s,timing_settings(p))
    def test_explicit_measured_rate_is_respected(self):
        p=self.project();p['settings']['speech_chars_per_second']=4.2;self.assertEqual(timing_settings(p)['speech_chars_per_second'],4.2)
    def test_unmodified_old_settings_keep_old_snapshot_shape(self):
        p={'settings':{'speech_chars_per_second':4.5,'action_seconds_per_beat':2.2}}
        self.assertEqual(timing_settings(p),{'episode_duration_mode':'story_driven','group_target_seconds':30,'speech_chars_per_second':4.5,'action_seconds_per_beat':2.2})
    def test_zero_speech_remains_action_not_zero_groups(self):
        p=self.project();p['groups']=[self.group('G',self.shot('A','',6))];r=build_plan(p);x=r['episodes'][0]
        self.assertEqual(x['planning_estimate_seconds'],6);self.assertEqual(x['minimum_groups_by_estimate'],1);self.assertIsNone(x['non_speech_budget_min']);self.assertEqual(x['status'],'待复核')
    def test_over_fifteen_percent_is_visible_not_deleted(self):
        p=self.project();s=self.shot('A','甲'*100,9);p['groups']=[self.group('G',s)]
        r=build_plan(p);self.assertEqual(r['episodes'][0]['non_speech_actual_seconds_estimate'],9);self.assertEqual(r['episodes'][0]['status'],'待复核');self.assertIn('NON_SPEECH_OVERAGE',[x['code'] for x in r['warnings']])
    def test_invalid_trim_cannot_remove_required_speech(self):
        p=self.project();p['groups']=[self.group('G',self.shot('A','甲'*125,2),trim_tail_seconds=4,trim_tail_basis='虚构示例')]
        self.assertIn('TRIM_CAPACITY',[x['code'] for x in build_plan(p)['issues']])
    def test_slow_long_dialogue_is_warning_not_automatic_cut(self):
        p=self.project();p['groups']=[self.group('G',self.shot('A','甲'*50,0))];r=build_plan(p)
        self.assertIn('SHOT_PACING_REVIEW',[x['code'] for x in r['warnings']]);self.assertEqual(len(p['groups'][0]['shots']),1)
    def test_timing_summary_exposes_ratio_and_tail(self):
        p=self.project();p['dialogues']=[{'id':'D','episode':1,'text':'甲'*250,'channel':'内心'}];s=render_summary(build_plan(p))
        for label in ('可发声字数','台词/OS秒','10%—15%','可剪尾'):self.assertIn(label,s)
    def test_cross_shot_spoken_text_fragments_reconstruct(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);save(base/'src.json',{'paragraphs':[{'id':'P1','text':'数字对白'}]})
            p={'schema_version':1,'source_path':'src.json','episodes':[{'id':1}],'scenes':[{'id':'S','episode':1,'name':'室内','time':'日','transition':'进入'}], 'assets':[],
               'dialogues':[{'id':'D','episode':1,'text':'有20元','spoken_text':'有二十元','speaker':'甲','channel':'现场','source_ids':['P1'],'evidence':'synthetic'}]}
            common={'scene_id':'S','entry_state':'站立','exit_state':'站立'}
            f1={'id':'D','text':'有20','spoken_text':'有二十','speaker':'甲','channel':'现场','emotion':'平静'};f2={'id':'D','text':'元','spoken_text':'元','speaker':'甲','channel':'现场','emotion':'平静'}
            p['groups']=[{'id':'G','episode':1,'expected_seconds':30,'shots':[common|{'id':'A','seconds':15,'speech':[f1]},common|{'id':'B','seconds':15,'speech':[f2]}]}]
            codes=lambda:{x['id'] for x in validate(p,base,stage='text')['issues']}
            self.assertNotIn('SPOKEN_FRAGMENT',codes());f2['spoken_text']='有二十元';self.assertIn('SPOKEN_FRAGMENT',codes())
            p['dialogues'][0].pop('spoken_text');f1['spoken_text']='有20';f2['spoken_text']='元';self.assertNotIn('SPOKEN_FRAGMENT',codes())
            f2['spoken_text']='有20元';self.assertIn('SPOKEN_FRAGMENT',codes())
            p['dialogues'][0]['spoken_text']=None;f2['spoken_text']='元';self.assertNotIn('SPOKEN_FRAGMENT',codes())
            f2['spoken_text']='有20元';self.assertIn('SPOKEN_FRAGMENT',codes())
    def test_conflicting_non_speech_ratios_rejected(self):
        p=self.project();p['settings']['non_speech_ratio_target']=.2
        with self.assertRaises(TimingError):timing_settings(p)

    def test_non_speech_change_invalidates_approval_snapshot(self):
        from workflow_gate import confirm_project,confirmed
        p=self.project();p['settings'].update(medium='真人',quality='自然写实',video_ratio='9:16',asset_ratio='16:9',scene_ratio='16:9',asset_review_mode='agent')
        confirm_project(p,'fictional user approval');confirmed(p)
        changes=[{'non_speech_ratio_target':.15},{'non_speech_ratio_min':.11,'non_speech_ratio_target':.11},
                 {'non_speech_ratio_max':.14},{'timing_mode':'legacy'}]
        for delta in changes:
            other=copy.deepcopy(p);other['settings'].update(delta)
            with self.assertRaises(ValueError):confirmed(other)
    def test_new_project_template_explicit_company_defaults(self):
        from core import load
        p=load(Path(__file__).resolve().parents[1]/'assets/project.template.json');s=timing_settings(p)
        self.assertEqual(s['timing_mode'],'company-speech-ratio');self.assertEqual(s['speech_chars_per_second'],5)
        self.assertEqual((s['non_speech_ratio_min'],s['non_speech_ratio_max'],s['non_speech_ratio_target']),(.1,.15,.1))
        self.assertNotIn('action_seconds_per_beat',s)
    def test_undeclared_trim_is_unknown_and_not_misrepresented_as_zero(self):
        p=self.project();p['groups']=[self.group('G',self.shot('A','甲'*50,0))];r=build_plan(p)
        self.assertIsNone(r['groups'][0]['trim_tail_seconds']);self.assertIsNone(r['groups'][0]['effective_story_seconds'])
        self.assertIsNone(r['episodes'][0]['trim_tail_seconds']);self.assertEqual(r['episodes'][0]['trim_tail_unknown_groups'],['G'])
        self.assertIn('UNACCOUNTED_HEADROOM',[x['code'] for x in r['warnings']]);self.assertEqual(r['episodes'][0]['status'],'待复核')
    def test_shared_group_tail_counted_in_last_episode_only(self):
        p=self.project();a=self.shot('A','甲'*50,1);a['seconds']=15;b=self.shot('B','乙'*50,1);b['seconds']=15
        g=self.group('G',a,trim_tail_seconds=4,trim_tail_basis='末镜尾部人工剪裁')
        g['shots']=[a,b];g['uses']=[{'episode':1,'shot_ids':['A']},{'episode':2,'shot_ids':['B']}];p['groups']=[g]
        report=build_plan(p);self.assertEqual(report['issues'],[]);self.assertIsNone(report['episodes'][0]['trim_tail_seconds']);self.assertEqual(report['episodes'][1]['trim_tail_seconds'],4);self.assertEqual(report['total_requests'],1)

    def test_registered_episode_with_no_dialogue_shows_explicit_exception(self):
        p=self.project();p['episodes']=[{'id':1},{'id':2}];p['dialogues']=[{'id':'D1','episode':1,'text':'甲'*50,'channel':'现场'}]
        rows=build_plan(p)['episode_dialogue_budgets'];self.assertEqual([x['episode'] for x in rows],[1,2])
        second=rows[1];self.assertEqual(second['spoken_characters'],0);self.assertIsNone(second['minimum_groups_by_budget']);self.assertIn('无对白',second['ratio_status'])
    def test_two_distinct_approved_dialogue_ids_count_as_two_utterances(self):
        p=self.project();p['dialogues']=[{'id':'D1','episode':1,'text':'好的','channel':'现场'},{'id':'D2','episode':1,'text':'好的','channel':'现场'}]
        self.assertEqual(build_plan(p)['episode_dialogue_budgets'][0]['spoken_characters'],4)

    def test_null_spoken_text_falls_back_to_dialogue_text(self):
        p=self.project();p['dialogues']=[{'id':'D','episode':1,'text':'你好','spoken_text':None,'channel':'现场'}]
        self.assertEqual(build_plan(p)['episode_dialogue_budgets'][0]['spoken_characters'],2)
        s=self.shot('S','你好',0);s['speech'][0]['spoken_text']=None
        self.assertEqual(estimate_shot(s,timing_settings(p))['spoken_characters'],2)

if __name__=='__main__':unittest.main()
