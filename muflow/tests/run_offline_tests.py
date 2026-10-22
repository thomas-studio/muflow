"""Offline behavioral tests. Original synthetic data + local mock HTTP only."""
from __future__ import annotations
import base64,copy,io,json,os,re,struct,sys,tempfile,threading,time,unittest,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import save,load,sha,select
from validate_project import validate
from build_preview import build as preview,compile_jobs
from export_assets import export
from batch_video import Store,run,download
from image_api import perform
from extract_source import extract
from setup_profile import pair
from PIL import Image

class Mock:
    def __init__(self):
        self.posts=[];self.attempts={};self.jobs={};self.lock=threading.Lock();self.edits=[];self.max_active=0
        atom=lambda kind,b:struct.pack('>I4s',len(b)+8,kind)+b
        self.content=atom(b'ftyp',b'isom0000')+atom(b'moov',b'LOCAL_TEST_NOT_PLAYABLE')+atom(b'mdat',bytes(range(256))*300)
        b=io.BytesIO();Image.new('RGB',(32,18),'gray').save(b,'PNG');self.png=b.getvalue()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def respond(self,data,code=200):
                b=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                if self.path=='/edit':
                    owner.edits.append((self.headers.get('Content-Type'),body));self.respond({'data':[{'b64':base64.b64encode(owner.png).decode()}]});return
                if self.path=='/uncertain':
                    owner.posts.append('uncertain');self.close_connection=True;self.connection.close();return
                data=json.loads(body);ep=int(re.search(r'第(\d+)集',data['prompt'])[1])
                with owner.lock:
                    owner.posts.append(ep);owner.attempts[ep]=owner.attempts.get(ep,0)+1;attempt=owner.attempts[ep]
                    tid='task-'+str(len(owner.jobs)+1);owner.jobs[tid]={'episode':ep,'attempt':attempt,'terminal':False}
                    owner.max_active=max(owner.max_active,sum(not j['terminal'] for j in owner.jobs.values()))
                self.respond({'id':tid,'status':'queued'})
            def do_GET(self):
                if self.path.startswith('/result/'):
                    start=int(re.search(r'bytes=(\d+)-',self.headers.get('Range','bytes=0-'))[1]);data=owner.content[start:]
                    self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{len(owner.content)-1}/{len(owner.content)}');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
                tid=self.path.rsplit('/',1)[-1]
                with owner.lock:
                    j=owner.jobs[tid]
                    if len({p for p in owner.posts if isinstance(p,int)})<5:self.respond({'id':tid,'status':'running'});return
                    j['terminal']=True
                    if j['episode']==1 and j['attempt']<3:self.respond({'id':tid,'status':'failed','message':'synthetic transient failure','cost':1});return
                    self.respond({'id':tid,'status':'completed','video_url':owner.url+'/result/'+tid,'cost':1})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()


def fixture(base,url):
    base=Path(base);(base/'prompts').mkdir();(base/'assets').mkdir();test_model='offline-'+uuid.uuid4().hex
    names=[('A1','小禾','role'),('A1·雨衣版','小禾·雨衣版','role'),('S1','储物室','scene'),('B1','值班员甲','extra')]
    assets=[]
    for n,(aid,name,kind) in enumerate(names):
        p=base/'assets'/('a'+str(n)+'.png');Image.new('RGB',(64,36),(n*50,100,150)).save(p)
        assets.append({'id':aid,'name':name,'kind':kind,'mode':'edit' if n==1 else 'generate','parent_id':'A1' if n==1 else None,
          'prompt':'仅用于离线测试的原创占位资产，不用于实际制作。','local_path':str(p),'sha256':sha(p),
          'remote_url':url+'/image/'+str(n),'review':{'status':'通过','evidence':'Synthetic test fixture, not a production image review'}})
    assets[1]['edit_audit']={'operation':'image_edit','endpoint':url+'/edit','input_asset_id':'A1','input_sha256':assets[0]['sha256'],'input_count':1}
    p={'schema_version':1,'project_id':'original-offline-test','version':'v1','source_path':'source.json',
       'settings':{'medium':'真人','quality':'自然摄影质感，细节清楚','scene_ratio':'16:9','video_ratio':'9:16','video_model':test_model},
       'episodes':[],'scenes':[],'dialogues':[],'assets':assets,'groups':[]}
    paragraphs=[]
    for ep in range(1,6):
        sid=f'{ep}-1';did=f'D{ep}';txt=f'这是第{ep}把钥匙。';src=f'SRC-{ep:06d}';role='A1' if ep<3 else 'A1·雨衣版';name=assets[0 if ep<3 else 1]['name'];gid=f'E{ep:02d}G01'
        paragraphs.append({'id':src,'text':'小禾说：“'+txt+'”'})
        p['episodes'].append({'id':ep,'title':'原创离线测试'})
        p['scenes'].append({'id':sid,'episode':ep,'name':'储物室','time':'白天','transition':'转场/承前已明确',
          'key_action':'小禾站在储物柜旁，将手中的钥匙放回固定挂钩，随后向门口退开。'})
        p['dialogues'].append({'id':did,'text':txt,'speaker':'小禾','channel':'现场','source_ids':[src],'emotion':'自然说明','evidence':'Original synthetic source explicitly names speaker'})
        file=base/'prompts'/(gid+'.md');file.write_text(f'第{ep}集·生成组01（30秒）\n@{name}=图1 / @储物室=图2\n原创离线测试提示词。',encoding='utf-8')
        shots=[{'id':gid+f'S{i}','scene_id':sid,'seconds':5,'asset_ids':[role,'S1'],
                'entry_state':'小禾站在柜旁，物品位置见前镜','exit_state':'小禾仍在柜旁，物品去向明确',
                'speech':[{'id':did,'text':txt,'speaker':'小禾','channel':'现场','emotion':'自然说明'}] if i==0 else []} for i in range(6)]
        p['groups'].append({'id':gid,'episode':ep,'expected_seconds':30,'shots':shots,'prompt_path':str(file),
             'bindings':[{'name':name,'asset_id':role},{'name':'储物室','asset_id':'S1'}],
             'review':{'status':'通过','evidence':'Synthetic structure test; not a real screenplay semantic review'}})
    save(base/'source.json',{'source_name':'原创测试短例','paragraphs':paragraphs});save(base/'project.json',p)
    cfg={'setup':{'status':'paired'},'api_key_env':'LOCAL_DRAMA_TEST_KEY','authorization':{'execution_enabled':True,'scopes':['video','images'],'budget_yuan':100,'evidence':'Offline mock server only'},
      'video':{'model':test_model,'resolution':'720p','concurrency':5,'verified_concurrency_limit':5,'verified_max_images':9,
       'verified_durations_seconds':[30],'max_price_per_attempt_yuan':1,'poll_seconds':0.02,'retry_seconds':0.02,'test_mode':True},
      'image':{'model':test_model+'_image','size':'64x36','max_price_per_attempt_yuan':1},
      'contracts':{'video':{'operation':'video','verified':True,'verification_evidence':'Local test fixture contract','url':url+'/video','poll_url':url+'/task/{task_id}',
       'body':{'model':'$model','prompt':'$prompt','images':'$images','duration':'$duration','ratio':'$ratio','resolution':'$resolution'},'id_paths':['id'],'status_paths':['status'],
       'result_url_paths':['video_url'],'cost_paths':['cost']},
       'image_generate':{'operation':'image_generate','verified':True,'verification_evidence':'Local synthetic generation contract','url':url+'/unused-generate','body':{'prompt':'$prompt'}},
       'image_edit':{'operation':'image_edit','verified':True,'verification_evidence':'Local multipart test contract','url':url+'/edit',
        'method':'POST','encoding':'multipart','body':{'model':'$model','prompt':'$prompt','image':'$input_file','ratio':'$ratio','size':'$size'},'result_b64_paths':['data.0.b64']}}}
    save(base/'profile.json',cfg);return p,cfg

class Cases(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.mock=Mock();self.p,self.cfg=fixture(self.base,self.mock.url);os.environ['LOCAL_DRAMA_TEST_KEY']='local-test-fake-value'
    def tearDown(self):
        self.mock.close()
        if self.base.resolve()!=Path(self.tmp.name).resolve() or self.base.resolve().parent!=Path(tempfile.gettempdir()).resolve():raise RuntimeError('Unexpected cleanup target')
        self.tmp.cleanup()
    def test_speaker_and_dialogue_integrity(self):
        self.assertEqual(validate(self.p,self.base)['issues'],[])
        bad=copy.deepcopy(self.p);bad['groups'][0]['shots'][0]['speech'][0]['speaker']='其他人'
        self.assertIn('SPEAKER_CHANNEL',[x['id'] for x in validate(bad,self.base)['issues']])
        bad=copy.deepcopy(self.p);bad['groups'][0]['shots'][0]['speech'][0]['text']='删词'
        self.assertIn('DIALOGUE_TEXT',[x['id'] for x in validate(bad,self.base)['issues']])
    def test_actual_edit_input_and_missing_input(self):
        r=perform(self.base/'project.json',self.base/'profile.json','A1·雨衣版',True)
        self.assertEqual(r['phase'],'saved');self.assertEqual(r['audit']['input_sha256'],self.p['assets'][0]['sha256'])
        self.assertIn((self.base/'assets/a0.png').read_bytes(),self.mock.edits[0][1])
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['image_edit']['body']={'prompt':'$prompt'};save(self.base/'badprofile.json',cfg)
        with self.assertRaisesRegex(ValueError,'actually pass'):perform(self.base/'project.json',self.base/'badprofile.json','A1·雨衣版')
        self.assertEqual(len(self.mock.edits),1)
    def test_edit_evidence_anchor_and_hash(self):
        bad=copy.deepcopy(self.p);bad['assets'][1]['edit_audit']['input_sha256']='wrong'
        self.assertIn('EDIT_AUDIT',[x['id'] for x in validate(bad,self.base)['issues']])
        path=Path(self.p['groups'][0]['prompt_path']);path.write_text(path.read_text().replace('=图2','=图3'),encoding='utf-8')
        self.assertIn('ANCHOR_ORDER',[x['id'] for x in validate(self.p,self.base)['issues']])
    def test_five_episodes_concurrent_third_attempt_and_resume(self):
        items=compile_jobs(self.p,self.base)['items'];store=Store(self.base/'production.sqlite')
        result=run(items,self.cfg,self.base/'video',store,max_seconds=8)
        self.assertTrue(all(x['phase']=='downloaded' for x in result['tasks']),result)
        self.assertEqual(set(self.mock.posts[:5]),{1,2,3,4,5});self.assertEqual(self.mock.attempts[1],3)
        self.assertLessEqual(self.mock.max_active,5);self.assertGreaterEqual(self.mock.max_active,5)
        before=len(self.mock.posts);run(items,self.cfg,self.base/'video',store,max_seconds=1);self.assertEqual(len(self.mock.posts),before)
    def test_unknown_submit_does_not_repeat(self):
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['video']['url']=self.mock.url+'/uncertain'
        item=compile_jobs(self.p,self.base)['items'][:1];store=Store(self.base/'unknown.sqlite')
        result=run(item,cfg,self.base/'unknown',store,max_seconds=1)
        self.assertEqual(result['tasks'][0]['phase'],'uncertain');run(item,cfg,self.base/'unknown',store,max_seconds=1)
        self.assertEqual(self.mock.posts,['uncertain'])
    def test_resume_range_transport(self):
        dest=self.base/'result.mp4';part=dest.with_name(dest.name+'.part');part.write_bytes(self.mock.content[:777])
        download(self.mock.url+'/result/example',dest)
        self.assertEqual(dest.read_bytes(),self.mock.content);self.assertFalse(part.exists())
    def test_preview_full_coverage_and_shared_request(self):
        result=preview(self.base/'project.json',self.base/'preview',self.base/'profile.json');self.assertEqual(result['episodes'],5)
        doc=(self.base/'preview/全剧跑前预览.html').read_text(encoding='utf-8')
        for ep in range(1,6):self.assertIn(f'id="ep-{ep}"',doc)
        self.assertIn('preview_resources/',doc);self.assertNotIn('data:image/jpeg;base64,',doc);self.assertNotIn('<script',doc)
        shared=copy.deepcopy(self.p);g=shared['groups'][0];g['uses']=[{'episode':1,'shot_ids':[s['id'] for s in g['shots'][:3]],'start_second':0,'end_second':15},
          {'episode':2,'shot_ids':[s['id'] for s in g['shots'][3:]],'start_second':15,'end_second':30}]
        selected=select(shared,'1-2');self.assertEqual(sum(x['id']==g['id'] for x in selected),1)
    def test_asset_export_exact_stage_and_extra_field(self):
        source={'剧名':'原创离线小例','表1 核心角色卡':{'rows':[{'编号':'A1','姓名':'小禾','生图提示词':'原创占位提示词'},
          {'编号':'A1·雨衣版','姓名':'小禾·雨衣版','生图提示词':'原图编辑占位提示词'}]},'表2 核心场景卡':{'rows':[{'编号':'S1','场景':'储物室','生图提示词':'原创空场景提示词'}]},
          '表6 一次性人物清单':{'rows':[{'编号':'B1','人物':'值班员甲','人数':'1','简描':'原创配角，灰色外套，中性站姿'}]}}
        result=export(source,self.p,self.base/'tables');self.assertEqual(result['edits'],1)
        data=load(self.base/'tables/资产清单源.json');stages={r['资产编号']:r for r in data['表5 资产溯源-角色']['rows']}
        self.assertTrue(stages['A1·雨衣版']['首次出现'].startswith('第03集'));self.assertTrue(stages['A1']['首次出现'].startswith('第01集'))
        self.assertEqual(data['表6 一次性人物清单']['rows'][0]['简描(给AI出图参考)'],'原创配角，灰色外套，中性站姿')
    def test_source_encoding_and_docx_anchors(self):
        import zipfile
        text='原创文本。\n第二段。'
        path=self.base/'original.txt';path.write_bytes(text.encode('gb18030'))
        result=extract(path);self.assertEqual([x['text'] for x in result['paragraphs']],text.splitlines())
        doc=self.base/'original.docx'
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格段</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml',xml)
        self.assertEqual([x['text'] for x in extract(doc)['paragraphs']],['第一段','表格段'])
    def test_install_pairing_requires_user_values_and_local_profile(self):
        with self.assertRaises(ValueError):pair({'schema_version':1,'setup':{'status':'unpaired'}},self.base/'blank.json')
        result=pair(self.cfg,self.base/'paired-profile.json')
        self.assertEqual(result['status'],'paired');self.assertEqual(result['external_requests'],0)
        cfg=load(self.base/'paired-profile.json');self.assertEqual(cfg['video']['model'],self.p['settings']['video_model'])
        bad=copy.deepcopy(self.cfg);bad['api_key']='real-value-not-allowed'
        with self.assertRaisesRegex(ValueError,'credential reference'):pair(bad,self.base/'secret-profile.json')
    def test_budget_is_shared_and_not_fixed_retry_limit(self):
        store=Store(self.base/'budget.sqlite');store.reserve('a',1,2);store.reserve('a',1,2);store.reserve('b',1,2)
        with self.assertRaisesRegex(ValueError,'budget'):store.reserve('c',1,2)
        self.assertEqual(store.reserved(),2)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Cases);result=unittest.TextTestRunner(verbosity=2).run(suite)
    target=Path(sys.argv[1]) if len(sys.argv)>1 else None
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
            'scope':'Original synthetic fixtures and 127.0.0.1 HTTP only. No paid calls, no production source/media.'}
    if target:save(target,report)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(not result.wasSuccessful())
