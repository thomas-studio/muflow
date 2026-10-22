"""Behavioral regression tests: real saved evidence with synthetic local fixtures only."""
import copy,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import save,load,sha,digest
from run_offline_tests import Mock,fixture
from test_v2 import upgrade,sync,synthetic_reviews
from test_helpers import event
from workflow_gate import *
from review_state import asset_ready,asset_subject,human_asset_record,readiness
from validate_project import validate
from asset_gallery import build as gallery
from build_preview import build
from execution_gate import record_delivery,snapshot,prepare

class Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.mock=Mock()
        self.p,self.cfg=fixture(self.base,self.mock.url);upgrade(self.p,self.cfg,self.base)
    def tearDown(self):self.mock.close();self.tmp.cleanup()
    def setting(self,value):
        self.p['settings']['asset_review_mode']=value;confirm_project(self.p,'Synthetic actual mode selection')
        mark_text_delivery(self.p,self.base,{'manifest_digest':digest(text_manifest(self.p,self.base))},'Synthetic revised settings preview')
        approve_text(self.p,self.base,event(self.base,'我已看过提示词，同意生图。'));sync(self.p,self.cfg,self.base)
    def test_manual_accepts_actual_human_adoption_without_ai_stamp(self):
        self.setting('manual')
        for a in self.p['assets']:a['review']={'status':'待复核'}
        self.assertTrue(images_ready(self.p,self.base))
    def test_agent_mode_does_not_fabricate_or_require_human_image_event(self):
        self.setting('agent');self.p['workflow'].pop('image_approval')
        self.assertTrue(images_ready(self.p,self.base));self.assertNotIn('image_approval',self.p['workflow'])
    def test_joint_requires_both_actual_review_sources(self):
        self.p['workflow'].pop('image_approval')
        with self.assertRaisesRegex(ValueError,'await user review'):images_ready(self.p,self.base)
    def test_template_pass_is_not_an_actual_ai_review(self):
        self.setting('agent');self.p['assets'][0]['review']={'status':'通过','evidence':'all fine'}
        with self.assertRaisesRegex(ValueError,'Actual image review'):images_ready(self.p,self.base)
    def test_review_must_observe_current_image_subject(self):
        self.setting('agent');self.p['assets'][0]['review']['subject_digest']='old'
        with self.assertRaisesRegex(ValueError,'Actual image review'):images_ready(self.p,self.base)
    def test_review_change_does_not_change_text_approval(self):
        self.p['assets'][0]['review']['checked_at']='new recorded time'
        self.assertIsNone(text_ready(self.p,self.base))
    def test_page_layout_refresh_does_not_invalidate_unchanged_image_adoption(self):
        first=human_asset_record(self.p,self.p['assets'][0]);sync(self.p,self.cfg,self.base)
        gallery(self.base/'project.json');gallery(self.base/'project.json')
        self.assertTrue(images_ready(self.p,self.base));self.assertEqual(first,human_asset_record(self.p,self.p['assets'][0]))
    def test_ai_record_completed_after_user_adoption_needs_no_repeat_human_event(self):
        self.p['assets'][0]['review']={'status':'待复核'}
        with self.assertRaises(ValueError):images_ready(self.p,self.base)
        original=self.p['workflow']['image_approval']['items'][0]['user_event']
        synthetic_reviews(self.p);self.assertTrue(images_ready(self.p,self.base))
        self.assertEqual(original,self.p['workflow']['image_approval']['items'][0]['user_event'])
    def test_only_changed_asset_subject_loses_its_adoption(self):
        a,b=self.p['assets'][:2];self.assertIsNotNone(human_asset_record(self.p,b));a['prompt']+=' new clothing'
        self.assertIsNone(human_asset_record(self.p,a));self.assertIsNotNone(human_asset_record(self.p,b))
    def test_partial_mother_adoption_is_possible_before_other_images_exist(self):
        a=self.p['assets'][1];a.update(local_path=None,sha256=None);sync(self.p,self.cfg,self.base)
        current=gallery(self.base/'project.json')['gallery'];mark_image_delivery(self.p,self.base,current,'Synthetic mother shown')
        approve_images(self.p,self.base,current,'Synthetic delivery',event(self.base,'图片确认采用。'),['A1'])
        self.assertIsNotNone(human_asset_record(self.p,self.p['assets'][0]))
    def test_local_preview_has_no_remote_or_image_acceptance_gate(self):
        for a in self.p['assets']:a.pop('remote_url',None);a['review']={'status':'待复核'}
        report=validate(self.p,self.base,stage='local');codes={i['id'] for i in report['issues']}
        self.assertNotIn('REMOTE_REFERENCE',codes);self.assertNotIn('IMAGE_REVIEW',codes)
        sync(self.p,self.cfg,self.base);r=build(self.base/'project.json',self.base/'preview')
        self.assertTrue((self.base/'preview/全剧跑前预览.html').is_file());self.assertFalse(r['prepared'])
    def test_independent_source_35_cannot_be_declared_complete_by_registering_5(self):
        self.p['episode_baseline']['episode_ids']=list(range(1,36))
        self.assertIn('SOURCE_SCOPE',{i['id'] for i in validate(self.p,self.base)['issues']})
        with self.assertRaisesRegex(ValueError,'Full source'):text_manifest(self.p,self.base)
    def test_semantic_template_pass_cannot_release_video(self):
        self.p['groups'][0]['review']={'status':'通过','evidence':'no teleport'}
        self.assertIn('SEMANTIC_REVIEW',{i['id'] for i in validate(self.p,self.base)['issues']})
    def test_each_shot_needs_recorded_semantic_coverage(self):
        for row in self.p['groups'][0]['review']['checks']:row['shot_ids']=[]
        self.assertIn('SEMANTIC_REVIEW',{i['id'] for i in validate(self.p,self.base)['issues']})
    def test_unresolved_user_feedback_blocks_same_image_and_full_label(self):
        aid=self.p['assets'][0]['id'];self.p['workflow']['asset_feedback']=[{'asset_id':aid,'status':'待处理','evidence':'Synthetic wrong collar','root_cause':'Synthetic fixture condition'}]
        with self.assertRaisesRegex(ValueError,'feedback unresolved'):images_ready(self.p,self.base)
        self.assertEqual(readiness(self.p,self.base)['label'],'半血')
    def test_full_local_preview_does_not_depend_on_oss_addresses(self):
        for a in self.p['assets']:a.pop('remote_url',None)
        self.assertEqual(readiness(self.p,self.base)['label'],'满血')
        self.assertIn('REMOTE_REFERENCE',{i['id'] for i in validate(self.p,self.base)['issues']})
    def test_review_metadata_and_panel_refresh_do_not_invalidate_video_start(self):
        sync(self.p,self.cfg,self.base);out=self.base/'preview';build(self.base/'project.json',out,self.base/'profile.json')
        receipt=record_delivery(out,'Synthetic actual delivered run panel')
        approve_video(self.p,self.base,snapshot(out),event(self.base,'我看过跑前预览，开始生成视频。'),receipt)
        self.p['assets'][0]['review']['checked_at']='Synthetic refreshed review timestamp';sync(self.p,self.cfg,self.base)
        build(self.base/'project.json',out,self.base/'profile.json')
        items,_=prepare(self.base/'project.json',self.cfg,out);self.assertEqual(len(items),5)
    def test_actual_delivery_copy_tamper_is_rejected(self):
        sync(self.p,self.cfg,self.base);out=self.base/'preview';build(self.base/'project.json',out,self.base/'profile.json')
        receipt=record_delivery(out,'Synthetic delivered run panel')
        approve_video(self.p,self.base,snapshot(out),event(self.base,'我已看过预览，开始生成视频。'),receipt);sync(self.p,self.cfg,self.base)
        Path(receipt['delivered_panel']).write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'delivery missing'):prepare(self.base/'project.json',self.cfg,out)
    def test_initial_two_panels_exist_with_unconfigured_api_and_no_registered_episodes(self):
        from init_project import initialize
        source=self.base/'novel.txt';source.write_text('原创合成小说。小禾把钥匙放进柜中。',encoding='utf-8')
        out=self.base/'new';result=initialize(out,source)
        self.assertEqual(result['media_calls'],0);self.assertTrue((out/'03_资产图/00_资产参考图总览.html').is_file())
        self.assertTrue((out/'07_跑前预览/全剧跑前预览.html').is_file());self.assertFalse((out/'profile.json').exists())
    def test_returned_mother_cannot_be_used_for_new_derivatives(self):
        from review_state import mother_ready
        a=self.p['assets'][0];self.p['workflow']['asset_feedback']=[{'asset_id':a['id'],'status':'待处理','evidence':'Synthetic returned image'}]
        with self.assertRaisesRegex(ValueError,'feedback unresolved'):mother_ready(self.p,self.base,a)
    def test_multi_asset_feedback_report_does_not_break_panel_refresh(self):
        from refresh_progress import refresh
        self.p['workflow']['asset_feedback']=[{'asset_ids':[a['id'] for a in self.p['assets'][:2]],'status':'待处理','evidence':'Synthetic joint issue'}]
        sync(self.p,self.cfg,self.base);result=refresh(self.base/'project.json')
        self.assertTrue(Path(result['review_report']).is_file());self.assertTrue((self.base/'99_工作区/面板刷新状态.json').is_file())
    def test_resolved_feedback_needs_actual_recheck_record(self):
        a=self.p['assets'][0];self.p['workflow']['asset_feedback']=[{'asset_id':a['id'],'status':'已解决','evidence':'Synthetic claim only'}]
        with self.assertRaisesRegex(ValueError,'resolution evidence'):asset_ready(self.p,a)
    def test_manual_summary_does_not_count_unselected_ai_reviews_as_pending(self):
        from selfcheck_report import project_records
        self.setting('manual')
        for a in self.p['assets']:a['review']={'status':'待复核'}
        checks,_=project_records(self.p)
        self.assertFalse(any(c['id']=='实际图片AI审核' for c in checks))
        self.assertTrue(all(c['status']=='通过' for c in checks if c['id']=='人工图片采用核验'))
    def test_text_preview_refreshes_fixed_entry_instead_of_creating_second_run_panel(self):
        from build_text_preview import build as text_build
        out=self.base/'separate_text_snapshot';result=text_build(self.base/'project.json',out)
        self.assertEqual(Path(result['panel']).resolve(),(self.base/'07_跑前预览/全剧跑前预览.html').resolve())
        self.assertFalse((out/'全剧跑前预览.html').exists());self.assertTrue((out/'text_review_snapshot.json').is_file())
    def test_no_regression_test_submits_to_live_provider(self):
        self.assertEqual(self.mock.posts,[]);self.assertEqual(self.mock.edits,[])

if __name__=='__main__':unittest.main()
