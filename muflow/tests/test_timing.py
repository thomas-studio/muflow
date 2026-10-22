"""Timing regressions from the reported unwanted uniform-episode prompt. No network."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import save
from workflow_gate import confirm_project,settings_snapshot
from timing_plan import *

class Timing(unittest.TestCase):
    def project(self):return {'settings':{'medium':'真人','quality':'自然中性影调','video_ratio':'9:16'}}
    def atom(self,aid,seconds):return {'id':aid,'speech':[],'seconds':30,'timing':{'action_seconds':seconds,'action_basis':'Original synthetic ordered action','allocation_basis':'Explicit synthetic acting/editing allowance'}}
    def settings(self):return timing_settings(self.project())
    def codes(self,p):return {x['code'] for x in build_plan(p)['issues']}
    def episode(self,ep,count,per):return [{'id':f'E{ep}G{i}','episode':ep,'expected_seconds':30,'shots':[self.atom(f'E{ep}G{i}S1',per)]} for i in range(count)]
    def test_no_uniform_episode_target_or_timing_question_required(self):
        p=self.project();c=confirm_project(p,'Synthetic user already gave ratio and visual style')
        self.assertEqual(c['status'],'confirmed');self.assertNotIn('episode_target_seconds',settings_snapshot(p));self.assertEqual(settings_snapshot(p)['group_target_seconds'],30)
    def test_default_estimates_are_four_point_five_and_two_point_two(self):
        s=self.settings();self.assertEqual(s['speech_chars_per_second'],4.5);self.assertEqual(s['action_seconds_per_beat'],2.2)
    def test_user_table_is_estimated_not_measured(self):
        for chars,beats,want in ((420,15,126.333333),(437,16,132.311111),(534,14,149.466667)):
            a={'speech':[{'text':'甲'*chars}],'timing':{'action_beats':beats,'action_basis':'Synthetic count matching reported estimate'}}
            seconds=estimate_shot(a,self.settings())['estimated_seconds'];self.assertAlmostEqual(seconds,want,places=5);self.assertEqual(minimum_groups(seconds),5)
    def test_variable_episode_results_150_300_and_330(self):
        p=self.project();p['groups']=self.episode(1,5,25.2)+self.episode(2,10,28)+self.episode(3,11,302/11)
        report=build_plan(p);self.assertEqual(report['issues'],[])
        self.assertEqual([r['planned_groups'] for r in report['episodes']],[5,10,11]);self.assertEqual([r['generated_seconds'] for r in report['episodes']],[150,300,330])
        self.assertAlmostEqual(report['episodes'][2]['estimated_seconds'],302);self.assertIn('非试读',report['basis'])
    def test_automatic_ordered_packing_needs_no_user_duration_choice(self):
        for n,want in ((126,5),(280,10),(302,11)):
            atoms=[self.atom('A'+str(i),1) for i in range(n)];groups=plan_atoms(atoms,self.settings())
            self.assertEqual(len(groups),want);self.assertEqual([i for g in groups for i in g['atom_ids']],[a['id'] for a in atoms]);self.assertTrue(all(g['expected_seconds']==30 for g in groups))
    def test_semantic_atomic_boundaries_can_need_more_than_ceiling(self):
        atoms=[self.atom('A'+str(i),16) for i in range(3)];groups=plan_atoms(atoms,self.settings())
        self.assertEqual(minimum_groups(48),2);self.assertEqual(len(groups),3)
    def test_long_atom_returns_for_semantic_split_without_rewriting(self):
        a=self.atom('long',31);before=copy.deepcopy(a)
        with self.assertRaisesRegex(TimingError,'语义拆分'):plan_atoms([a],self.settings())
        self.assertEqual(a,before)
    def test_uniform_150_target_cannot_be_reintroduced(self):
        p=self.project();p['settings']['episode_target_seconds']=150
        self.assertIn('EPISODE_DURATION_MODE',self.codes(p))
        with self.assertRaises(TimingError):confirm_project(p,'Synthetic attempt to impose old uniform duration')
    def test_request_ten_or_fifteen_is_not_a_new_user_choice(self):
        for seconds in (10,15):
            p=self.project();p['settings']['group_target_seconds']=seconds;self.assertIn('REQUEST_SECONDS',self.codes(p))
    def test_duration_exception_cannot_bypass_fixed_request(self):
        p=self.project();p['groups']=self.episode(1,1,10);p['groups'][0].update(expected_seconds=15,duration_exception={'evidence':'Synthetic agent exception'})
        self.assertIn('REQUEST_SECONDS',self.codes(p))
    def test_episode_total_room_does_not_hide_local_overload(self):
        p=self.project();p['groups']=self.episode(1,2,1);p['groups'][0]['shots'][0]['timing']['action_seconds']=31
        r=build_plan(p);self.assertLess(r['episodes'][0]['estimated_seconds'],60);self.assertIn('TIMING_CAPACITY',self.codes(p));self.assertEqual(r['episodes'][0]['status'],'不通过')
    def test_group_thirty_but_one_shot_too_short_fails(self):
        p=self.project();a=self.atom('A',16);a['seconds']=15;b=self.atom('B',10);b['seconds']=15
        p['groups']=[{'id':'G','episode':1,'expected_seconds':30,'shots':[a,b]}];self.assertIn('TIMING_CAPACITY',self.codes(p))
    def test_actual_overlap_is_deducted_once(self):
        a={'speech':[{'text':'甲'*45}],'timing':{'action_beats':2,'action_basis':'Synthetic turn during speech','overlap_seconds':4.4,'overlap_evidence':'This turn occurs during the same spoken sentence'}}
        self.assertAlmostEqual(estimate_shot(a,self.settings())['estimated_seconds'],10)
    def test_overlap_without_evidence_or_beyond_shorter_component_fails(self):
        a={'speech':[{'text':'甲'*45}],'timing':{'action_beats':2,'action_basis':'Synthetic turn','overlap_seconds':4}}
        with self.assertRaises(TimingError):estimate_shot(a,self.settings())
        a['timing'].update(overlap_seconds=5,overlap_evidence='Synthetic invalid overlap')
        with self.assertRaises(TimingError):estimate_shot(a,self.settings())
    def test_extra_pauses_and_transition_need_basis(self):
        a=self.atom('A',2);a['timing']['pause_seconds']=1
        with self.assertRaises(TimingError):estimate_shot(a,self.settings())
        a['timing'].update(pause_basis='Explicit dramatic pause',transition_seconds=1,transition_basis='Explicit scene establishment')
        self.assertEqual(estimate_shot(a,self.settings())['estimated_seconds'],4)
    def test_empty_padding_and_unjustified_headroom_fail(self):
        p=self.project();p['groups']=self.episode(1,1,10);a=p['groups'][0]['shots'][0]
        a['timing']['padding_only']=True;self.assertIn('EMPTY_PADDING',self.codes(p))
        a['timing'].pop('padding_only');a['timing'].pop('allocation_basis');self.assertIn('TIMING_HEADROOM',self.codes(p))
    def test_missing_action_assessment_cannot_pass_as_speech_only(self):
        p=self.project();p['groups']=self.episode(1,1,10);p['groups'][0]['shots'][0].pop('timing');self.assertIn('TIMING_MISSING',self.codes(p))
    def test_zero_need_and_nonfinite_values_fail(self):
        for value in (0,float('nan'),float('inf'),-1):
            with self.assertRaises(TimingError):estimate_shot(self.atom('A',value),self.settings())
    def test_spoken_expansion_and_punctuation_are_used(self):
        a=self.atom('A',1);a['speech']=[{'text':'20，！','spoken_text':'二十'}]
        r=estimate_shot(a,self.settings());self.assertEqual(r['spoken_characters'],2);self.assertAlmostEqual(r['speech_seconds_estimate'],2/4.5)
    def test_new_template_has_no_fixed_episode_target(self):
        p=load(Path(__file__).resolve().parents[1]/'assets/project.template.json');self.assertNotIn('episode_target_seconds',p['settings']);self.assertEqual(p['settings']['group_target_seconds'],30);self.assertEqual(p['settings']['episode_duration_mode'],'story_driven')
    def test_preview_shows_variable_episode_values_and_real_estimate(self):
        p=self.project();p['groups']=self.episode(1,5,25.2)+self.episode(2,10,28);doc=render_summary(build_plan(p))
        self.assertIn('150',doc);self.assertIn('300',doc);self.assertIn('126.0',doc);self.assertIn('估算',doc)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Timing))
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),'scope':'Fixed30/story-driven estimates, synthetic ordered atoms, settings and preview functions; no network/media/model execution.'}
    if len(sys.argv)>1:save(sys.argv[1],report)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(not result.wasSuccessful())
