"""V2 regressions from observed failure patterns, using original synthetic fixtures."""
import copy,hashlib,io,json,struct,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_offline_tests import Mock,fixture
from core import save,load,sha,digest
from compose_requests import compose_group
from continuity import audit
from validate_project import validate
from build_preview import build,compile_jobs
from execution_gate import prepare,record_delivery as raw_record_delivery,snapshot
from workflow_gate import confirm_project,text_manifest,mark_text_delivery,approve_text,approve_images,approve_video,DELIVERABLES
from batch_video import run,Store
from image_api import perform
from media_integrity import check_mp4,download
from speech_metrics import estimate,spoken_count
from version_catalog import build as catalog
from change_guard import authorize
from test_helpers import event
from asset_gallery import build as asset_gallery
from workflow_gate import mark_image_delivery

def record_delivery(directory,evidence):
    receipt=raw_record_delivery(directory,evidence);pp=Path(directory).parent/'project.json';p=load(pp)
    approve_video(p,pp.parent,snapshot(directory),event(pp.parent,'我已看过跑前面板，开始生成本范围视频。'),receipt)
    save(pp,p);return receipt

def synthetic_reviews(p):
    """Explicit synthetic observations for these regression fixtures, never production evidence."""
    from review_state import asset_subject,group_subject
    import datetime
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    for a in p['assets']:
        rules=['identity_stage','appearance']+(['space'] if a['kind'] in ('scene','vehicle') else [])+(['video_reference'] if a.get('purpose')=='identity_sheet' else [])
        a['review']={'status':'通过','subject_digest':asset_subject(p,a),'performed_by':'agent','checked_at':stamp,
          'evidence':'Synthetic fixture: fixed local pixels inspected by fixture construction; not production evidence',
          'checks':[{'rule_id':r,'expected':'Synthetic original cabinet fixture '+r,'observed':'Synthetic fixture bytes and authored baseline agree for '+a['id'],
                     'evidence':'Synthetic local regression '+a['local_path'],'result':'通过'} for r in rules]}
    for g in p['groups']:
        rules=['story_continuity','space']+(['speaker_channel'] if any(s.get('speech') for s in g['shots']) else [])+(['physical_sound'] if any(s.get('sound_effects') for s in g['shots']) else [])
        g['review']={'status':'通过','subject_digest':group_subject(p,g),'performed_by':'agent','checked_at':stamp,
          'evidence':'Synthetic fixture storage continuity construction; not real project review',
          'checks':[{'rule_id':r,'expected':'Synthetic fixed source and cabinet state '+r,'observed':'Synthetic fixture '+g['id']+' retains authored speaker and pose',
                     'shot_ids':[s['id'] for s in g['shots']],'evidence':'Synthetic in-memory fixture group '+g['id'],'result':'通过'} for r in rules]}

def stage_fixture(p,base):
    p['settings'].pop('episode_target_seconds',None)
    p['settings'].update(asset_ratio='16:9',episode_duration_mode='story_driven',asset_review_mode='joint')
    p['episode_baseline']={'confirmed':True,'episode_ids':[e['id'] for e in p['episodes']],'source_sha256':sha(base/p['source_path']),'evidence':'Synthetic explicitly confirmed baseline, not inferred from registration'}
    synthetic_reviews(p)
    p['input_assets']=[];p['workflow']={'mode':'staged_user_review'};p['deliverables']={}
    (base/'text').mkdir(exist_ok=True)
    for k in DELIVERABLES:
        f=base/'text'/(k+'.txt');f.write_text('Synthetic text-stage artifact: '+k,encoding='utf-8');p['deliverables'][k]=str(f)
    confirm_project(p,'Synthetic user confirmed video/style/timing; not a production approval')
    mark_text_delivery(p,base,{'manifest_digest':digest(text_manifest(p,base))},'Synthetic full text package delivered')
    approve_text(p,base,event(base,'我已看过两类提示词，同意生图。'))
    save(base/'project.json',p);gallery=asset_gallery(base/'project.json')['gallery']
    mark_image_delivery(p,base,gallery,'Synthetic actual complete image gallery delivered')
    approve_images(p,base,gallery,'Synthetic gallery delivered',event(base,'图片确认通过，采用本批图片。'))

def upgrade(p,cfg,base):
    p['schema_version']=2;p['entities']={'person':{'kind':'person','identity_id':'person'}};p['iterations']=[]
    p['adaptation_review']={'status':'通过','evidence':'Synthetic fixture only: no production semantic claim'}
    p['settings'].update(onscreen_text=False,subtitles=False,background_music=False,ambient_bed=False,group_target_seconds=30,speech_chars_per_second=5)
    for a in p['assets']:
        a['remote_sha256']=a['sha256'];a['remote_reference_evidence']='Synthetic local mock transport mapping, not real upload'
        if a['kind']=='role':a.update(entity_id='person',state_key='rain' if a['parent_id'] else 'base',purpose='video_reference')
        if a['kind']=='scene':a['spatial_review']={'status':'通过','evidence':'Synthetic flat storage scene'}
    for ep,d in enumerate(p['dialogues'],1):d['episode']=ep
    prev=None
    for g in p['groups']:
        role=g['bindings'][0]['asset_id'];state='rain' if role!='A1' else 'base'
        for s in g['shots']:
            s['timing']={'action_beats':1,'action_basis':'Synthetic fixture cabinet action, not a production timing result','allocation_basis':'Synthetic acting/editing margin, used only in regression fixtures'}
            s.update(camera='固定中景，主体小禾',action='小禾在储物柜旁保持原位。',visible_entities=['person'],camera_subjects=['person'],events=[],sound_effects=[])
            s['state_in']={'entities':{'person':{'where':'storage:by_cabinet','pose':'standing','visual_state':state,'reference_asset_id':role}}}
            s['state_out']=copy.deepcopy(s['state_in'])
            if prev and prev['state_out']!=s['state_in']:
                s['entry_bridge']=[{'action':'change_costume','description':'另次送物前已换上雨衣，制作省略有明确转场。','changes':[
                    {'path':'/entities/person/visual_state','before':'base','after':'rain'},
                    {'path':'/entities/person/reference_asset_id','before':'A1','after':'A1·雨衣版'}]}]
            prev=s
    cfg['authorization'].update(mode='project_pre_authorized',project_scope={'project_id':p['project_id'],'episodes':[1,2,3,4,5]},allow_content_redo=False)
    cfg['contracts']['video']['body']['client_key']='$client_key'
    stage_fixture(p,base)
    sync(p,cfg,base)

def sync(p,cfg,base):
    for g in p['groups']:Path(g['prompt_path']).write_text(compose_group(p,g),encoding='utf-8',newline='\n')
    save(base/'project.json',p);save(base/'profile.json',cfg)

class V2(unittest.TestCase):
    def setUp(self):
        import os
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.mock=Mock();self.p,self.cfg=fixture(self.base,self.mock.url)
        os.environ['LOCAL_DRAMA_TEST_KEY']='local-test-fake-value';upgrade(self.p,self.cfg,self.base)
    def tearDown(self):
        self.mock.close()
        if self.base.resolve().parent!=Path(tempfile.gettempdir()).resolve():raise RuntimeError('Unexpected cleanup target')
        self.tmp.cleanup()
    def preview(self):
        build(self.base/'project.json',self.base/'preview',self.base/'profile.json');record_delivery(self.base/'preview','Offline test simulates an actually delivered local panel, not user consent')
        return prepare(self.base/'project.json',self.cfg,self.base/'preview')
    def codes(self):return {e['id'] for e in validate(self.p,self.base)['issues']}
    def test_valid_v2_compiled_source_and_states(self):
        self.assertEqual(self.codes(),set());items,grant=self.preview();self.assertEqual(len(items),5)
        self.assertEqual([f['id'] for f in items[0]['speech_segments']],['D1'])
    def test_whole_registered_line_missing_selected_episode(self):
        d=copy.deepcopy(self.p['dialogues'][0]);d.update(id='D_missing',text='下一句话也必须发出。');self.p['dialogues'].insert(1,d)
        report=validate(self.p,self.base,[self.p['groups'][0]])
        self.assertIn('DIALOGUE_OMITTED',{x['id'] for x in report['issues']})
    def test_actual_request_dropped_speech_blocked(self):
        g=self.p['groups'][0];file=Path(g['prompt_path']);file.write_text(file.read_text(encoding='utf-8').replace('这是第1把钥匙。',''),encoding='utf-8')
        self.assertIn('REQUEST_DRIFT',self.codes())
    def test_deleted_reference_old_anchor_blocked(self):
        self.p['groups'][0]['bindings'].pop()
        self.assertTrue({'SHOT_BINDING','ANCHOR_ORDER'}<=self.codes())
    def test_boundary_pose_teleport_blocked(self):
        s=self.p['groups'][0]['shots'][1];s['state_in']['entities']['person']['pose']='seated';s['state_out']=copy.deepcopy(s['state_in'])
        self.assertIn('BOUNDARY_STATE',self.codes())
    def test_undocumented_child_handoff_blocked(self):
        s=self.p['groups'][0]['shots'][0];s['state_in']['entities']['child']={'where':'storage:by_cabinet','holder':'person'}
        s['state_out']['entities']['child']={'where':'street','holder':'person'}
        codes={x[0] for x in audit(self.p,[self.p['groups'][0]])}
        self.assertTrue({'STATE_EVENTS','CHILD_HOLDER'}<=codes)
    def test_duplicate_identity_blocked(self):
        self.p['entities']['copy']={'kind':'person','identity_id':'person'}
        s=self.p['groups'][0]['shots'][0];s['visible_entities'].append('copy')
        self.assertIn('DUPLICATE_PERSON',self.codes())
    def test_remote_reference_hash_and_approved_episode(self):
        self.p['assets'][0]['remote_sha256']='different';self.p['dialogues'][0]['episode']=2
        self.assertTrue({'REMOTE_REFERENCE','DIALOGUE_WRONG_EPISODE'}<=self.codes())
    def test_wrong_seat_door_and_duplicate_occupancy(self):
        s=self.p['groups'][0]['shots'][0]
        s['events']=[{'action':'alight','description':'从副驾下车','entity_id':'person','vehicle_id':'car','seat':'front_right','door':'left_front','changes':[]}]
        self.assertIn('VEHICLE_DOOR',self.codes())
        s['state_in']['entities']['person'].update(where='vehicle:car',seat='front_left')
        s['state_in']['entities']['other']={'where':'vehicle:car','seat':'front_left'}
        self.assertIn('SEAT_DUPLICATE',self.codes())
    def test_state_reference_mismatch_and_injury_disappearance(self):
        s=self.p['groups'][0]['shots'][0];s['state_in']['entities']['person']['visual_state']='rain'
        s['state_in']['entities']['person']['injury']='old_scar';s['state_out']['entities']['person']['injury']='none'
        self.assertTrue({'STATE_ASSET','STATE_EVENTS'}<=self.codes())
    def test_camera_subject_is_not_silent_bystander(self):
        self.p['groups'][0]['shots'][0]['camera_subjects']=['absent_listener']
        self.assertIn('CAMERA_SUBJECT',self.codes())
    def test_configured_thirty_not_fifteen_and_no_ambient_bed(self):
        self.p['groups'][0]['expected_seconds']=15;self.p['settings']['ambient_bed']=True
        self.assertTrue({'REQUEST_SECONDS','AUDIO_VISUAL_POLICY'}<=self.codes())
    def test_punctuation_count_and_capacity(self):
        text='甲'*521+'，'*96;stats=estimate(text,6.5)
        self.assertEqual(stats['written_characters'],617);self.assertEqual(stats['spoken_character_estimate'],521);self.assertAlmostEqual(stats['speech_seconds_estimate'],80.153846,places=5)
        self.assertEqual(spoken_count('你好，世 界！￥'),4)
        self.p['settings']['speech_chars_per_second']=0.1
        self.assertIn('SPEECH_CAPACITY',self.codes())
    def test_no_delivery_and_wrong_scope_never_submit(self):
        build(self.base/'project.json',self.base/'preview',self.base/'profile.json')
        with self.assertRaises(FileNotFoundError):prepare(self.base/'project.json',self.cfg,self.base/'preview')
        record_delivery(self.base/'preview','Synthetic local delivery')
        cfg=copy.deepcopy(self.cfg);cfg['authorization']['project_scope']['episodes']=[1]
        with self.assertRaisesRegex(ValueError,'range'):prepare(self.base/'project.json',cfg,self.base/'preview')
        self.assertEqual(self.mock.posts,[])
    def test_stale_preview_and_unapproved_paid_redo(self):
        self.preview();self.p['groups'][0]['directing_notes']='改变动作版本';save(self.base/'project.json',self.p)
        with self.assertRaisesRegex(ValueError,'stale'):prepare(self.base/'project.json',self.cfg,self.base/'preview')
        self.p['groups'][0]['revision_kind']='content_redo';stage_fixture(self.p,self.base);sync(self.p,self.cfg,self.base)
        build(self.base/'project.json',self.base/'preview2',self.base/'profile.json');record_delivery(self.base/'preview2','Synthetic new delivery')
        with self.assertRaisesRegex(ValueError,'content redo'):prepare(self.base/'project.json',self.cfg,self.base/'preview2')
    def test_direct_runner_cannot_skip_gate(self):
        with self.assertRaisesRegex(ValueError,'execution gate'):run(compile_jobs(self.p,self.base)['items'],self.cfg,self.base/'videos',Store(self.base/'tasks.sqlite'),max_seconds=0.1)
        self.assertEqual(self.mock.posts,[])
    def test_authorized_pool_durable_intent_and_unique_receipts(self):
        items,grant=self.preview();store=Store(self.base/'tasks.sqlite');seen=[]
        from batch_video import api_json as original
        def observer(url,method='GET',body=None,*args,**kw):
            if method=='POST':
                jid=next(i['id'] for i in items if i['prompt']==body['prompt']);d=store.get(jid)
                self.assertEqual(d['phase'],'submitting');self.assertEqual(body['client_key'],d['client_key']);seen.append(jid)
            return original(url,method,body,*args,**kw)
        with patch('batch_video.api_json',side_effect=observer):r=run(items,self.cfg,self.base/'videos',store,max_seconds=8,grant=grant)
        self.assertTrue(all(t['phase']=='downloaded' for t in r['tasks']),r)
        self.assertEqual(len(seen),7);self.assertEqual(self.mock.attempts[1],3);self.assertEqual(set(self.mock.posts[:5]),{1,2,3,4,5})
        receipts=[p for t in r['tasks'] for a in t['history'] for p in a['receipts']]
        self.assertEqual(len(receipts),len(set(receipts)));self.assertTrue(all(Path(p).exists() for p in receipts))
    def test_front_moov_truncated_mdat_not_delivered(self):
        broken=self.mock.content[:-200];self.mock.content=broken
        p=self.base/'broken.mp4'
        with self.assertRaisesRegex(ValueError,'Truncated'):download(self.mock.url+'/result/file',p)
        self.assertFalse(p.exists());self.assertTrue(list(self.base.glob('broken.mp4.part.corrupt-*')))
    def test_existing_truncated_file_recovers_same_result(self):
        dest=self.base/'recover.mp4';dest.write_bytes(self.mock.content[:-100])
        download(self.mock.url+'/result/same-task',dest)
        self.assertEqual(dest.read_bytes(),self.mock.content);self.assertTrue(list(self.base.glob('recover.mp4.corrupt-*')));self.assertEqual(self.mock.posts,[])
    def test_valid_but_changed_file_is_not_silently_adopted(self):
        dest=self.base/'changed.mp4';original=self.mock.content;expected=hashlib.sha256(original).hexdigest();dest.write_bytes(original[:-1]+b'Z')
        download(self.mock.url+'/result/same-task',dest,expected_sha256=expected)
        self.assertEqual(dest.read_bytes(),original);self.assertTrue(list(self.base.glob('changed.mp4.corrupt-*')));self.assertEqual(self.mock.posts,[])
    def test_range_length_and_etag_identity(self):
        class Response(io.BytesIO):pass
        dest=self.base/'file.mp4';part=dest.with_name(dest.name+'.part');part.write_bytes(b'abc');save(part.with_name(part.name+'.meta.json'),{'etag':'old','total':100})
        r=Response(b'123');r.status=206;r.headers={'Content-Range':'bytes 3-99/100','Content-Length':'97','ETag':'new'}
        with patch('media_integrity.urllib.request.urlopen',return_value=r):
            with self.assertRaisesRegex(ValueError,'identity changed'):download('http://127.0.0.1/result',dest)
        self.assertFalse(dest.exists())
    def test_reuse_calls_no_api_and_real_edit_still_required(self):
        a=self.p['assets'][0];a['mode']='reuse';save(self.base/'project.json',self.p)
        r=perform(self.base/'project.json',self.base/'missing-profile.json',a['id'],True)
        self.assertEqual(r['external_requests'],0);self.assertEqual(self.mock.edits,[])
        self.p['assets'][1]['mode']='generate';save(self.base/'project.json',self.p)
        with self.assertRaisesRegex(ValueError,'image editing'):perform(self.base/'project.json',self.base/'profile.json',self.p['assets'][1]['id'])
    def test_all_iterations_visible_user_approval_not_invented(self):
        a=self.p['assets'][0];self.p['iterations']=[{'id':'try1','label':'首次构图','reason':'衣服错误，保留供复盘','status':'智能体建议退回','kind':'image','local_path':a['local_path'],'sha256':a['sha256']},
          {'id':'try2','label':'衣服修订','reason':'等待用户确认','status':'待用户审阅','kind':'image','local_path':a['local_path'],'sha256':a['sha256']}];save(self.base/'project.json',self.p)
        catalog(self.base/'project.json',self.base/'catalog.html');doc=(self.base/'catalog.html').read_text(encoding='utf-8')
        self.assertIn('首次构图',doc);self.assertIn('衣服修订',doc)
        self.p['iterations'][1]['status']='用户采用';save(self.base/'project.json',self.p)
        with self.assertRaisesRegex(ValueError,'user approval'):catalog(self.base/'project.json',self.base/'catalog2.html')
    def test_billing_reserve_charge_refund_unknown_separated(self):
        store=Store(self.base/'cost.sqlite');store.reserve('a',2,10);store.reserve('b',2,10)
        store.record_billing('a','reported_charge',2,'synthetic charge receipt');store.record_billing('a','reported_charge',2,'same reported state again');store.record_billing('a','reported_refund',1,'synthetic cumulative refund receipt')
        b=store.billing();self.assertEqual(b['reserved_upper_bound_yuan'],4);self.assertEqual(b['reported_net_yuan'],1);self.assertEqual(b['unknown_charge_attempts'],['b'])
    def test_literal_reference_hint_is_not_an_edit_input(self):
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['image_edit']['body']={'prompt':'$prompt','image':'文字中提到 $input_b64 并不传文件'};save(self.base/'badprofile.json',cfg)
        with self.assertRaisesRegex(ValueError,'actually pass'):perform(self.base/'project.json',self.base/'badprofile.json','A1·雨衣版')
        self.assertEqual(self.mock.edits,[])
    def test_video_contract_must_transmit_all_images_and_actual_ratio(self):
        build(self.base/'project.json',self.base/'preview',self.base/'profile.json');record_delivery(self.base/'preview','Synthetic local panel')
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['video']['body'].pop('ratio')
        with self.assertRaisesRegex(ValueError,'actual ratio'):prepare(self.base/'project.json',cfg,self.base/'preview')
        cfg=copy.deepcopy(self.cfg);cfg['contracts']['video']['body']['images']='$first_image'
        with self.assertRaisesRegex(ValueError,'every bound reference'):prepare(self.base/'project.json',cfg,self.base/'preview')
        self.assertEqual(self.mock.posts,[])
    def test_reuse_export_is_not_a_generation_task(self):
        from export_assets import export
        self.p['assets'][0]['mode']='reuse'
        source={'表1 核心角色卡':{'rows':[{'编号':'A1','姓名':'小禾','生图提示词':'原创参考'}]}}
        export(source,self.p,self.base/'tables');task=load(self.base/'tables/资产图_出图任务.json')['items'][0]
        self.assertEqual(task['operation'],'reuse');self.assertFalse(task['generation_required']);self.assertEqual(task['sha256'],self.p['assets'][0]['sha256'])
    def test_unexecuted_check_never_reported_as_pass(self):
        from selfcheck_report import build as report
        file=self.base/'fake-checks.json';save(file,{'checks':[{'id':'never_ran','object':'fixture','version':'v1','status':'通过','evidence':'没有真实执行时间'}]})
        text=report([file]);self.assertIn('待复核：1',text);self.assertNotIn('通过：1',text)
    def test_skill_change_needs_both_full_scope_and_code(self):
        salt='offline-test-salt';token='synthetic-offline-token';policy={'salt':salt,'iterations':1000,'verifier':hashlib.pbkdf2_hmac('sha256',token.encode(),salt.encode(),1000).hex()}
        req={'fully_authorized':True,'scope':'skill_modification','evidence':'Synthetic test permission','target_version':'test','change_plan_sha256':'0'*64}
        self.assertTrue(authorize(req,token,policy)['verified'])
        for q,c in [(dict(req,fully_authorized=False),token),(req,'wrong'),(dict(req,scope='drama_edit'),token)]:
            with self.assertRaises(ValueError):authorize(q,c,policy)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(V2))
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
      'scope':'Original synthetic content/local mock HTTP only. No live provider, video content evaluation or production-file changes.'}
    if len(sys.argv)>1:save(sys.argv[1],report)
    print(json.dumps(report,ensure_ascii=False));raise SystemExit(not result.wasSuccessful())
