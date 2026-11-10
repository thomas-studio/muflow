"""Concurrency, retry, identity and file-lock regression. Local fixtures only."""
import base64,concurrent.futures as cf,copy,io,os,sys,tempfile,threading,time,unittest,urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from PIL import Image
from core import save,load,sha
from batch_video import Store,run
from build_preview import compile_jobs
from image_api import perform
from rate_limit import rejected_429
from stage_queue import start_batch,complete_image,next_batch,QUEUE
import test_stage_queue as stage_cases
from run_offline_tests import Mock,fixture

class VideoParallel(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.mock=Mock();self.p,self.cfg=fixture(self.base,self.mock.url);os.environ['LOCAL_DRAMA_TEST_KEY']='synthetic-local-only'
    def tearDown(self):self.mock.close();self.tmp.cleanup()
    def test_all_twelve_submitted_without_legacy_cap(self):
        item=compile_jobs(self.p,self.base)['items'][0];items=[]
        for i in range(12):
            x=copy.deepcopy(item);x['id']='parallel-'+str(i);items.append(x)
        self.cfg['video']['concurrency']=2;self.cfg['video']['verified_concurrency_limit']=2
        barrier=threading.Barrier(12,timeout=8);guard=threading.Lock();arrived=[]
        def fake(url,method='GET',body=None,key=None,timeout=None,headers=None):
            with guard:arrived.append(body['prompt'])
            barrier.wait()
            return {'status':'completed','video_url':self.mock.url+'/result/test','cost':1}
        with patch('batch_video.api_json',side_effect=fake):r=run(items,self.cfg,self.base/'video12',Store(self.base/'12.sqlite'),max_seconds=12)
        self.assertEqual(len(arrived),12);self.assertTrue(all(x['phase']=='downloaded' for x in r['tasks']),r)
    def test_confirmed_no_charge_429_retries_and_releases_reservation(self):
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['video']['rate_limit']={'http_429_rejected_before_task':True,'rejection_evidence':'local synthetic fixture','http_429_no_charge':True,'no_charge_evidence':'local synthetic fixture'}
        calls=[];headers=Message();headers['Retry-After']='0.01'
        def fake(url,method='GET',body=None,key=None,timeout=None,headers=None):
            calls.append(1)
            if len(calls)==1:raise urllib.error.HTTPError(url,429,'synthetic',hdrs,None)
            return {'status':'completed','video_url':self.mock.url+'/result/test','cost':1}
        hdrs=headers;item=compile_jobs(self.p,self.base)['items'][:1];store=Store(self.base/'retry.sqlite')
        with patch('batch_video.api_json',side_effect=fake):r=run(item,cfg,self.base/'retry',store,max_seconds=6)
        self.assertEqual(len(calls),2);self.assertEqual(r['tasks'][0]['phase'],'downloaded');self.assertEqual(store.reserved(),1);self.assertEqual(store.billing()['reported_charge_yuan'],1);self.assertEqual(store.billing()['unknown_charge_attempts'],[])
    def test_unverified_429_is_uncertain_and_never_reposted(self):
        calls=[];item=compile_jobs(self.p,self.base)['items'][:1];store=Store(self.base/'uncertain429.sqlite')
        def fake(url,method='GET',body=None,key=None,timeout=None,headers=None):
            calls.append(1);raise urllib.error.HTTPError(url,429,'synthetic',Message(),None)
        with patch('batch_video.api_json',side_effect=fake):
            first=run(item,self.cfg,self.base/'uncertain429',store,max_seconds=2);second=run(item,self.cfg,self.base/'uncertain429',store,max_seconds=2)
        self.assertEqual(first['tasks'][0]['phase'],'uncertain');self.assertEqual(second['tasks'][0]['phase'],'uncertain');self.assertEqual(len(calls),1);self.assertEqual(store.reserved(),1)
    def test_atomic_budget_under_parallel_reservation(self):
        store=Store(self.base/'atomic.sqlite');barrier=threading.Barrier(12)
        def reserve(i):
            barrier.wait()
            try:store.reserve('p'+str(i),1,2);return True
            except ValueError:return False
        with cf.ThreadPoolExecutor(max_workers=12) as pool:r=list(pool.map(reserve,range(12)))
        self.assertEqual(sum(r),2);self.assertEqual(store.reserved(),2)
    def test_image_429_retries_same_asset_without_losing_attempt(self):
        cfg=copy.deepcopy(self.cfg);cfg['image']['retry_seconds']=0.01;c=cfg['contracts']['image_generate'];c.update(url=self.mock.url+'/image',body={'prompt':'$prompt'},result_b64_paths=['data.0.b64'],rate_limit={'http_429_rejected_before_task':True,'rejection_evidence':'local fixture','http_429_no_charge':True,'no_charge_evidence':'local fixture'});save(self.base/'improfile.json',cfg)
        calls=[]
        def fake(url,method='GET',body=None,key=None,timeout=None,headers=None):
            calls.append(1)
            if len(calls)==1:raise urllib.error.HTTPError(url,429,'synthetic',Message(),None)
            return {'data':[{'b64':base64.b64encode(self.mock.png).decode()}]}
        with patch('image_api.api_json',side_effect=fake),patch('image_api._refresh',return_value={}):
            first=perform(self.base/'project.json',self.base/'improfile.json','A1',True);time.sleep(.03);second=perform(self.base/'project.json',self.base/'improfile.json','A1',True)
        self.assertEqual(first['phase'],'retry');self.assertEqual(second['phase'],'saved');self.assertEqual(second['attempts'],2);self.assertEqual(len(calls),2)
        self.assertEqual(Store(self.base/'production.sqlite').reserved(),1)

    def test_image_billing_uses_reported_actual_and_flags_overage(self):
        cfg=copy.deepcopy(self.cfg);c=cfg['contracts']['image_generate'];c.update(url=self.mock.url+'/image',body={'prompt':'$prompt'},result_b64_paths=['data.0.b64'],cost_paths=['cost']);save(self.base/'costprofile.json',cfg)
        response={'data':[{'b64':base64.b64encode(self.mock.png).decode()}],'cost':1.25}
        with patch('image_api.api_json',return_value=response),patch('image_api._refresh',return_value={}):r=perform(self.base/'project.json',self.base/'costprofile.json','A1',True)
        store=Store(self.base/'production.sqlite');self.assertTrue(r['price_exceeded']);self.assertEqual(store.reserved(),1.25);self.assertEqual(store.billing()['reported_charge_yuan'],1.25)

class ImageScheduling(unittest.TestCase):
    def setup_case(self):
        c=stage_cases.StageQueue('test_first_five_complete_automatically_exposes_next_five_not_downstream');c.setUp();self.addCleanup(c.tearDown);return c
    def test_partial_completion_unlocks_child_before_sibling_finished(self):
        c=self.setup_case();mother=c.add_image('mother');c.add_image('slow');c.add_image('child','mother');r=c.finish_text_stages();start_batch(c.pp,r['batch_id'])
        a=c.artifact({'id':r['batch_id']},'image',asset_id='mother');mother.update(local_path=a['path'],sha256=a['sha256']);c.review(mother);c.sync()
        complete_image(c.pp,r['batch_id'],'mother',a,'实际合成图完成，模拟已审核母图')
        r2=next_batch(c.pp);self.assertEqual(r2['action'],'ready');self.assertEqual(r2['asset_ids'],['child'])
        q=load(c.base/QUEUE);self.assertEqual(q['stages']['images']['items']['slow']['status'],'running')
    def run_image_pool(self,target,expected_peak):
        c=self.setup_case()
        for i in range(21):c.add_image('p'+str(i))
        c.p['schema_version']=2;c.sync();c.finish_text_stages()
        profile={'setup':{'status':'paired'},'authorization':{'execution_enabled':True,'scopes':['images'],'budget_yuan':100,'evidence':'offline synthetic','project_scope':{'project_id':c.p['project_id']}},'image':{}}
        if target is not ...:profile['image']['concurrency']=target
        prof=c.base/'profile.json';save(prof,profile);lock=threading.Lock();inflight=0;peak=0;started=0
        gate=threading.Barrier(expected_peak,timeout=8)
        def fake(pp,pr,aid,execute):
            nonlocal inflight,peak,started
            with lock:inflight+=1;started+=1;n=started;peak=max(peak,inflight)
            if n<=expected_peak:gate.wait()
            f=c.base/(aid+'-actual.png');Image.new('RGB',(32,18),'blue').save(f);d={'phase':'saved','output':str(f),'sha256':sha(f),'audit':{'operation':'image_generate'}}
            from asset_gallery import register_saved
            register_saved(pp,aid,d)
            with lock:inflight-=1
            return d
        from batch_images import run as image_run
        with patch('workflow_gate.confirmed'),patch('workflow_gate.text_ready'),patch('audit_project_network.require_clean'),patch('batch_images.perform',side_effect=fake),patch('asset_gallery._build_unlocked',return_value={}):
            result=image_run(c.pp,prof,True,max_seconds=12)
        self.assertEqual(started,21);self.assertEqual(peak,expected_peak);self.assertEqual(result['blocked'],{})
        q=load(c.base/QUEUE);self.assertTrue(all(x['status']=='completed' for x in q['stages']['images']['items'].values()));self.assertEqual(len(load(c.pp)['iterations']),21)
    def test_default_ten_rolls_through_twenty_one(self):self.run_image_pool(...,10)
    def test_unbounded_runs_all_twenty_one(self):self.run_image_pool(None,21)


class AtomicReplaceTests(unittest.TestCase):
    def test_existing_empty_lock_is_not_modified(self):
        from batch_video import exclusive
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'existing.lock';f.write_bytes(b'')
            with exclusive(f):self.assertEqual(f.stat().st_size,0)
            self.assertEqual(f.read_bytes(),b'')
    @unittest.skipUnless(os.name=='nt','Windows atomic replacement behavior')
    def test_windows_transient_conflict_retries_same_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'atomic.json';actual=os.replace;seen=[]
            def flaky(src,target):
                seen.append(str(src))
                if len(seen)<3:raise PermissionError('synthetic temporary reader')
                return actual(src,target)
            with patch('core.os.replace',side_effect=flaky),patch('core.time.sleep'):
                save(dest,{'value':42})
            self.assertEqual(len(seen),3);self.assertEqual(len(set(seen)),1);self.assertEqual(load(dest),{'value':42})
    @unittest.skipUnless(os.name=='nt','Windows atomic replacement behavior')
    def test_persistent_conflict_is_reported(self):
        with tempfile.TemporaryDirectory() as d,patch('core.os.replace',side_effect=PermissionError('persistent')),patch('core.time.sleep'):
            with self.assertRaises(PermissionError):save(Path(d)/'atomic.json',{'x':1})

if __name__=='__main__':unittest.main()
