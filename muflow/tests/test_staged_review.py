"""Meaningful stage-boundary tests, with local synthetic assets and mock HTTP only."""
import copy,io,json,os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from PIL import Image
from run_offline_tests import Mock,fixture
from test_v2 import upgrade
from core import save,load,sha,digest
from workflow_gate import *
from build_text_preview import build as text_preview
from build_preview import build as video_preview
from execution_gate import prepare,record_delivery,snapshot
from image_api import perform
from test_helpers import event

class Stages(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.mock=Mock();self.p,self.cfg=fixture(self.base,self.mock.url);upgrade(self.p,self.cfg,self.base)
        os.environ['LOCAL_DRAMA_TEST_KEY']='local-test-fake-value'
        self.cfg['contracts']['image_generate']={'operation':'image_generate','verified':True,'verification_evidence':'Local synthetic API only','url':self.mock.url+'/edit',
          'body':{'model':'$model','prompt':'$prompt','ratio':'$ratio','size':'$size'},'result_b64_paths':['data.0.b64']}
        self.sync()
    def tearDown(self):
        self.mock.close()
        if self.base.resolve().parent!=Path(tempfile.gettempdir()).resolve():raise RuntimeError('Unexpected cleanup target')
        self.tmp.cleanup()
    def sync(self):save(self.base/'project.json',self.p);save(self.base/'profile.json',self.cfg)
    def image(self,aid='A1'):self.sync();return perform(self.base/'project.json',self.base/'profile.json',aid,True)
    def no_calls(self):self.assertEqual(self.mock.posts,[]);self.assertEqual(self.mock.edits,[])
    def reapprove_text(self):
        mark_text_delivery(self.p,self.base,{'manifest_digest':digest(text_manifest(self.p,self.base))},'Synthetic user actually received revised complete text')
        approve_text(self.p,self.base,event(self.base,'我确认已看过新版提示词，同意生图。'))
    def video(self,start=False,episodes=None):
        self.sync();out=self.base/'preview';video_preview(self.base/'project.json',out,self.base/'profile.json',episodes);receipt=record_delivery(out,'Synthetic actually delivered full video preview')
        if start:approve_video(self.p,self.base,snapshot(out),event(self.base,'我已看过跑前预览，开始生成视频。'),receipt);self.sync()
        return prepare(self.base/'project.json',self.cfg,out,episodes)
    def locked_face(self):
        a=self.p['assets'][0];a.update(mode='reuse',origin='user_provided')
        self.p['input_assets']=[{'asset_id':a['id'],'entity_id':'person','local_path':a['local_path'],'sha256':a['sha256'],'identity_locked':True}]
        self.reapprove_text();return a
    def test_read_settings_before_generation(self):
        self.p['workflow'].pop('project_confirmation')
        with self.assertRaisesRegex(ValueError,'settings not confirmed'):self.image()
        self.no_calls()
    def test_changed_video_ratio_requires_confirmation_and_not_asset_ratio_copy(self):
        self.p['settings']['video_ratio']='16:9'
        with self.assertRaisesRegex(ValueError,'settings not confirmed'):self.image()
        self.no_calls()
    def test_missing_api_pairing_has_no_generation(self):
        self.cfg['setup']['status']='unpaired'
        with self.assertRaisesRegex(ValueError,'pairing incomplete'):self.image()
        self.no_calls()
    def test_text_preview_without_any_api_configuration(self):
        (self.base/'profile.json').unlink();self.p['workflow'].pop('text_approval');self.sync();(self.base/'profile.json').unlink()
        r=text_preview(self.base/'project.json',self.base/'text-preview')
        self.assertEqual(r['media_calls'],0);self.assertEqual(len(r['manifest']['requests']),5);self.assertEqual(len(r['manifest']['assets']),4)
        self.assertEqual(len(r['manifest']['timing_plan']['episodes']),5);self.assertNotIn('episode_target_seconds',r['manifest']['settings'])
        self.assertIn('全剧跑前预览.html',__import__('urllib.parse',fromlist=['unquote']).unquote((self.base/'text-preview/全剧文字与提示词审阅.html').read_text(encoding='utf-8')))
        self.assertIn('逐集30秒分组',Path(r['panel']).read_text(encoding='utf-8'))
        self.no_calls()
    def test_missing_asset_table_blocks_text_delivery(self):
        self.p['deliverables'].pop('asset_table')
        with self.assertRaisesRegex(ValueError,'asset_table'):text_manifest(self.p,self.base)
        self.no_calls()
    def test_text_not_approved_cannot_generate_even_with_budget(self):
        self.p['workflow'].pop('text_approval')
        with self.assertRaisesRegex(ValueError,'wait for the user'):self.image()
        self.no_calls()
    def test_changed_asset_prompt_invalidates_approval(self):
        self.p['assets'][0]['prompt']='Changed clothing proposal after review'
        with self.assertRaisesRegex(ValueError,'wait for the user'):self.image()
        self.no_calls()
    def test_changed_text_deliverable_invalidates_approval(self):
        Path(self.p['deliverables']['storyboard']).write_text('Unapproved revised storyboard',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'wait for the user'):self.image()
        self.no_calls()
    def test_asset_image_actual_request_uses_sixteen_nine_with_portrait_video(self):
        self.assertEqual(self.p['settings']['video_ratio'],'9:16');result=self.image()
        self.assertEqual(result['phase'],'saved');sent=json.loads(self.mock.edits[0][1]);self.assertEqual(sent['ratio'],'16:9')
    def test_contract_omitting_approved_prompt_never_submits(self):
        self.cfg['contracts']['image_generate']['body'].pop('prompt')
        with self.assertRaisesRegex(ValueError,'approved prompt'):self.image()
        self.no_calls()
    def test_contract_omitting_asset_ratio_never_submits(self):
        self.cfg['contracts']['image_generate']['body'].pop('ratio')
        with self.assertRaisesRegex(ValueError,'confirmed asset ratio'):self.image()
        self.no_calls()
    def test_supplied_face_reused_without_api_and_never_randomly_rebuilt(self):
        a=self.locked_face();self.sync();r=perform(self.base/'project.json',self.base/'absent.json',a['id'],True)
        self.assertEqual(r['external_requests'],0)
        clone=copy.deepcopy(a);clone.update(id='fresh-clone',mode='generate',origin=None,parent_id=None);self.p['assets'].append(clone);self.reapprove_text()
        with self.assertRaisesRegex(ValueError,'actual image editing'):self.image('fresh-clone')
        self.no_calls()
    def test_approved_derived_image_actually_uses_supplied_face_bytes(self):
        a=self.locked_face();r=self.image('A1·雨衣版')
        self.assertEqual(r['audit']['input_sha256'],a['sha256']);self.assertIn(Path(a['local_path']).read_bytes(),self.mock.edits[0][1])
    def test_generated_images_need_user_review_before_video(self):
        self.p['workflow'].pop('image_approval')
        with self.assertRaisesRegex(ValueError,'await user review'):self.video()
        self.no_calls()
    def test_image_approval_does_not_allow_video_by_itself(self):
        with self.assertRaisesRegex(ValueError,'Video preview/start awaiting'):self.video()
        self.no_calls()
    def test_partial_preview_cannot_stand_for_full_series(self):
        with self.assertRaisesRegex(ValueError,'Full user-defined review scope'):self.video(start=True,episodes='1')
        self.no_calls()
    def test_actual_wrong_generated_ratio_is_not_approvable(self):
        a=self.p['assets'][0];Image.new('RGB',(36,64),'gray').save(a['local_path']);a['sha256']=sha(a['local_path'])
        with self.assertRaisesRegex(ValueError,'aspect ratio'):approve_images(self.p,self.base,str(self.base/'synthetic-gallery.html'),'Synthetic gallery shown','Synthetic attempted approval')
        self.no_calls()
    def test_complete_review_pipeline_prepares_video_without_new_media(self):
        items,grant=self.video(start=True)
        self.assertEqual(len(items),5);self.assertEqual(grant['items_digest'],digest(items));self.no_calls()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Stages))
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
            'scope':'Stage entry points with synthetic fixtures/local mock API. Not an actual WorkBuddy agent tool-selection run.'}
    if len(sys.argv)>1:save(sys.argv[1],report)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(not result.wasSuccessful())
