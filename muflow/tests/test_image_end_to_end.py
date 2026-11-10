"""Exercise real image execution locally with fake network responses only."""
import base64,io,os,sys,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from PIL import Image
from core import save,load
from batch_images import run
from batch_video import Store
import test_stage_queue as stage_cases

class RealImageDispatch(unittest.TestCase):
    def test_ten_real_perform_calls_produce_registered_files_once(self):
        c=stage_cases.StageQueue('test_first_five_complete_automatically_exposes_next_five_not_downstream');c.setUp();self.addCleanup(c.tearDown)
        for i in range(12):c.add_image('actual'+str(i))
        c.p['schema_version']=2;c.p['settings']['asset_ratio']='16:9';c.sync();c.finish_text_stages()
        cfg={'setup':{'status':'paired'},'api_key_env':'MUFLOW_SYNTHETIC_TEST_KEY',
          'authorization':{'execution_enabled':True,'scopes':['images'],'budget_yuan':100,'evidence':'合成离线测试','project_scope':{'project_id':c.p['project_id']}},
          'image':{'model':'synthetic','size':'synthetic','max_price_per_attempt_yuan':1},
          'contracts':{'image_generate':{'verified':True,'operation':'image_generate','url':'http://127.0.0.1/synthetic','method':'POST',
             'verification_evidence':'合成测试契约','body':{'prompt':'$prompt','model':'$model','ratio':'$ratio'},'result_b64_paths':['b64']}}}
        profile=c.base/'profile.json';save(profile,cfg);bio=io.BytesIO();Image.new('RGB',(32,18),'blue').save(bio,format='PNG');encoded=base64.b64encode(bio.getvalue()).decode()
        lock=threading.Lock();barrier=threading.Barrier(10,timeout=15);counter=[0,0,0]
        def fake_post(url,method='GET',body=None,key=None,timeout=None,headers=None):
            with lock:counter[0]+=1;counter[1]+=1;counter[2]=max(counter[2],counter[1]);number=counter[0]
            if number<=10:barrier.wait()
            with lock:counter[1]-=1
            return {'b64':encoded}
        with patch.dict(os.environ,{'MUFLOW_SYNTHETIC_TEST_KEY':'synthetic'}),patch('workflow_gate.confirmed'),patch('workflow_gate.text_ready'),patch('workflow_gate.image_generation',return_value='16:9'),patch('audit_project_network.require_clean'),patch('image_api.api_json',side_effect=fake_post),patch('refresh_progress.refresh',return_value={}),patch('asset_gallery._build_unlocked',return_value={}):
            result=run(c.pp,profile,True,max_seconds=30)
        self.assertEqual(counter[0],12);self.assertEqual(counter[2],10);self.assertFalse(result['blocked'],result)
        q=load(c.base/'stage_queue.json');self.assertTrue(all(x['status']=='completed' for x in q['stages']['images']['items'].values()))
        self.assertEqual(len(load(c.pp)['iterations']),12);store=Store(c.base/'production.sqlite')
        self.assertEqual(store.reserved(),12);self.assertTrue(all(store.get('image:actual'+str(i))['phase']=='saved' for i in range(12)))

if __name__=='__main__':unittest.main()
