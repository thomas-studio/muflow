"""Integrated local tests: owned progress, actual image registration and stable video preview."""
import base64,io,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from PIL import Image
from core import load,save,sha
from init_project import initialize
from stage_queue import next_batch,start_batch,QUEUE
from image_api import perform
from batch_video import Store
from refresh_progress import refresh_owned

class OwnedRefreshIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.src=self.base/'source.txt';self.src.write_text('第1集\n院子里有人走来。\n第2集\n她推开门。',encoding='utf-8')
        result=initialize(self.base,self.src,'owned-integration');self.assertEqual(result['status'],'complete',result)
        self.pp=self.base/'project.json';os.environ['OWNED_LOCAL_TEST_KEY']='offline-synthetic'
    def tearDown(self):
        os.environ.pop('OWNED_LOCAL_TEST_KEY',None);self.tmp.cleanup()
    def test_stage_start_refreshes_real_previews_and_keeps_user_files(self):
        panel=self.base/'07_跑前预览'/'全剧跑前预览.html';prior=panel.read_text(encoding='utf-8')
        ready=next_batch(self.pp);start_batch(self.pp,ready['batch_id'])
        current=panel.read_text(encoding='utf-8');self.assertIn('运行中 1',current);self.assertNotEqual(current,prior)
        records=[__import__('json').loads(x) for x in (self.base/'99_工作区'/'阶段队列_面板刷新记录.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[-1]['change'],'batch_started');self.assertEqual(records[-1]['status'],'通过')
        p=load(self.pp);meta=p['muflow_init_v240'];state=load(self.base/meta['state_path'])
        for rel in ('03_资产图/00_资产参考图总览.html','07_跑前预览/全剧跑前预览.html','08_视频/已出成片预览.html'):
            self.assertEqual(state['owned_files'][rel],sha(self.base/rel))
    def test_image_actual_registration_and_video_increment(self):
        p=load(self.pp);p['schema_version']=2;p['settings'].update(asset_ratio='16:9',scene_ratio='16:9')
        p['assets']=[{'id':'A_SYN','name':'合成角色','kind':'role','mode':'generate','prompt':'合成测试人物','entity_id':'e1','state_key':'base','review':{'status':'待复核'}}];save(self.pp,p)
        profile={'api_key_env':'OWNED_LOCAL_TEST_KEY','setup':{'status':'paired'},'image':{'model':'synthetic-image','size':'64x36','max_price_per_attempt_yuan':1},
          'authorization':{'execution_enabled':True,'scopes':['images'],'budget_yuan':20,'evidence':'offline synthetic test','project_scope':{'project_id':'owned-integration'}},
          'contracts':{'image_generate':{'operation':'image_generate','verified':True,'verification_evidence':'synthetic test','url':'http://127.0.0.1/image','body':{'model':'$model','prompt':'$prompt','ratio':'$ratio'},'result_b64_paths':['data.0.b64']}}}
        prof=self.base/'profile.json';save(prof,profile);b=io.BytesIO();Image.new('RGB',(64,36),'blue').save(b,'PNG');response={'data':[{'b64':base64.b64encode(b.getvalue()).decode()}]}
        with patch('workflow_gate.image_generation',return_value='16:9'),patch('image_api.api_json',return_value=response):result=perform(self.pp,prof,'A_SYN',True)
        self.assertEqual(result['phase'],'saved');p=load(self.pp);a=p['assets'][0];self.assertEqual(a['sha256'],sha(a['local_path']))
        gallery=self.base/'03_资产图'/'00_资产参考图总览.html';self.assertIn('合成角色',gallery.read_text(encoding='utf-8'))
        old_html=sha(self.base/'08_视频'/'已出成片预览.html');vid=self.base/'sample.mp4';vid.write_bytes(b'SYNTHETIC FILE - NOT PLAYABLE')
        store=Store(self.base/'production.sqlite');store.put('G_SYN','fp',{'kind':'video','episode':1,'phase':'downloaded','output':str(vid),'sha256':sha(vid),'bytes':vid.stat().st_size,'attempts':1,'history':[]})
        result=refresh_owned(self.pp);self.assertFalse(result.get('issues'),result);self.assertEqual(sha(self.base/'08_视频'/'已出成片预览.html'),old_html)
        self.assertIn('G_SYN',(self.base/'08_视频'/'成片进度.js').read_text(encoding='utf-8'))
        meta=load(self.pp)['muflow_init_v240'];state=load(self.base/meta['state_path']);self.assertEqual(state['owned_files']['08_视频/成片进度.js'],sha(self.base/'08_视频'/'成片进度.js'))

    def _cli(self,script,*args):
        skill=Path(__file__).resolve().parents[1]
        return subprocess.run([sys.executable,'-X','utf8',str(skill/'scripts'/script),str(self.pp),*[str(a) for a in args]],capture_output=True,text=True,encoding='utf-8',timeout=15)
    def test_all_three_public_clis_use_owned_publisher(self):
        calls=[('asset_gallery.py',[]),('build_preview.py',[self.base/'07_跑前预览']),('video_gallery.py',[])]
        for script,args in calls:
            result=self._cli(script,*args);self.assertEqual(result.returncode,0,result.stderr+' '+result.stdout)
            parsed=__import__('json').loads(result.stdout);self.assertFalse(parsed['issues'],parsed)
        meta=load(self.pp)['muflow_init_v240'];state=load(self.base/meta['state_path'])
        for rel in ('03_资产图/00_资产参考图总览.html','07_跑前预览/全剧跑前预览.html','08_视频/已出成片预览.html'):
            self.assertEqual(state['owned_files'][rel],sha(self.base/rel))
    def test_public_clis_do_not_overwrite_user_changed_or_legacy_panels(self):
        panel=self.base/'03_资产图'/'00_资产参考图总览.html';panel.write_text('用户修改的原页面',encoding='utf-8');before=panel.read_bytes()
        result=self._cli('asset_gallery.py');self.assertNotEqual(result.returncode,0);self.assertEqual(panel.read_bytes(),before)
        p=load(self.pp);del p['muflow_init_v240'];save(self.pp,p)
        for script,args in [('asset_gallery.py',[]),('build_preview.py',[self.base/'07_跑前预览']),('video_gallery.py',[])]:
            result=self._cli(script,*args);self.assertNotEqual(result.returncode,0);self.assertEqual(panel.read_bytes(),before)
    def test_public_clis_reject_unsupported_scope_and_summary(self):
        for script,args in [('build_preview.py',[self.base/'07_跑前预览','--episodes','1']),('build_preview.py',[self.base/'unowned-target']),('video_gallery.py',['--summary',self.base/'some-summary.json'])]:
            result=self._cli(script,*args);self.assertNotEqual(result.returncode,0);self.assertIn('不支持' if '--episodes' in args or '--summary' in args else '固定',result.stderr)

if __name__=='__main__':unittest.main()
