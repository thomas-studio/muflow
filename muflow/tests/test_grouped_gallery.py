"""Grouped gallery semantics with real local bytes, read-only jobs and zero API calls."""
import copy,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote,unquote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from PIL import Image
from core import save,load,sha,digest
from asset_gallery import build,verify,manifest,required_ids,register_saved,GALLERY,SNAPSHOT,Cards

class GroupedGallery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.pp=self.base/'project.json'
        self.p={'project_id':'gallery-local','version':'v2.3.0','settings':{'asset_review_mode':'joint'},'episodes':[{'id':1},{'id':2}],
            'workflow':{'production_scope':{'episodes':[1],'user_evidence':'Synthetic explicit selected scope'}},
            'entities':{'hero':{'kind':'person','identity_id':'hero'},'hero_alias':{'kind':'person','identity_id':'hero'},'other':{'kind':'person','identity_id':'other'}},
            'assets':[],'iterations':[],'groups':[]}
        # Deliberately shuffled source order and identical Chinese names: grouping must use identity lineage.
        self.add('rain','同名人物·雨衣','role','hero_alias','mother',color='blue')
        self.add('stranger','同名人物','role','other',color='red')
        self.add('mother','同名人物','role','hero',color='green')
        self.add('early','同名人物·早期衣服','role','hero','mother',color='yellow')
        self.add('room','空房','scene',color='gray');self.add('bag','黑袋','prop',color='black');self.add('car','车','vehicle',color='white')
        self.add('outside','范围外','role','outsider',color='purple')
        self.p['groups']=[{'id':'G1','episode':1,'bindings':[{'asset_id':x} for x in ('early','rain','stranger','room','bag','car')],
             'shots':[{'id':'S1','asset_ids':['early','stranger','room','bag','car']},{'id':'S2','asset_ids':['rain']}]},
            {'id':'G2','episode':2,'bindings':[{'asset_id':'outside'}],'shots':[{'id':'S3','asset_ids':['outside']}]}]
        self.gallery=self.base/'03_资产图'/GALLERY
    def tearDown(self):self.tmp.cleanup()
    def add(self,aid,name,kind,entity=None,parent=None,color=None):
        row={'id':aid,'name':name,'kind':kind,'entity_id':entity,'parent_id':parent,'mode':'edit' if parent else 'generate','prompt':'原创离线提示词 '+aid,'version':'v1','review':{'status':'待复核','evidence':''}}
        if color:
            f=self.base/(aid+'.png');Image.new('RGB',(64,36),color).save(f);row.update(local_path=str(f),sha256=sha(f))
        self.p['assets'].append(row);return row
    def asset(self,aid):return next(a for a in self.p['assets'] if a['id']==aid)
    def build(self):save(self.pp,self.p);return build(self.pp)
    def parse(self):
        parser=Cards();parser.feed(self.gallery.read_text(encoding='utf-8'));return parser
    def iteration(self,i,aid,path=None,status='待复核',reason='保留原始理由'):
        a=self.asset(aid);path=Path(path or a['local_path'])
        row={'id':i,'asset_id':aid,'kind':'image','label':'尝试 '+i,'status':status,'reason':reason,'local_path':str(path),'sha256':sha(path),'prompt':'当时实际提示词','raw_request':{'input_asset_id':'mother','detail':'保留完整原始字段'}}
        self.p['iterations'].append(row);return row
    def resign(self):
        snap=load(self.gallery.parent/SNAPSHOT);snap['panel_sha256']=sha(self.gallery);save(self.gallery.parent/SNAPSHOT,snap)
    def jobs(self,rows):
        c=sqlite3.connect(self.base/'production.sqlite')
        try:
            c.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,fingerprint TEXT,data TEXT)')
            for aid,row in rows.items():c.execute('INSERT INTO jobs VALUES (?,?,?)',('image:'+aid,'local',json.dumps(row)))
            c.commit()
        finally:c.close()
    def test_identity_mother_then_story_order_not_name_or_source_order(self):
        self.build();m=manifest(self.p,self.base);rows={a['id']:a for a in m['cards']}
        self.assertEqual(rows['rain']['group_key'],rows['mother']['group_key']);self.assertEqual(rows['early']['group_key'],rows['mother']['group_key'])
        self.assertNotEqual(rows['stranger']['group_key'],rows['mother']['group_key'])
        cards=self.parse().cards;self.assertLess(cards.index('mother'),cards.index('early'));self.assertLess(cards.index('early'),cards.index('rain'))
        self.assertEqual(rows['rain']['identity_lineage'],['mother','rain']);self.assertEqual(rows['rain']['first_use']['shot_id'],'S2')
        self.assertEqual(required_ids(self.p),{'mother','early','rain','stranger','room','bag','car'})
        doc=self.gallery.read_text(encoding='utf-8');self.assertIn('data-section="scene"',doc);self.assertIn('data-section="prop"',doc);self.assertIn('data-section="vehicle"',doc)
        verify(self.p,self.base,self.gallery)
    def test_click_targets_open_the_actual_local_original_bytes(self):
        self.iteration('same','mother');self.build();snap=verify(self.p,self.base,self.gallery)
        by_key={r['key']:r for r in snap['visible_images']}
        for image in self.parse().actual_images:
            target=self.gallery.parent/unquote(image['href'])
            self.assertEqual(sha(target),by_key[image['key']]['sha256'])
            with Image.open(target) as original:original.verify()
    def test_human_adoption_renders_separately_without_mutating_asset_manifest(self):
        from review_state import asset_subject
        self.build();before=manifest(self.p,self.base);a=self.asset('mother')
        self.p['workflow']['image_approval']={'items':[{'asset_id':'mother','subject_digest':asset_subject(self.p,a),'sha256':a['sha256'],'status':'approved','user_evidence':'实际用户采用证据'}]}
        self.assertEqual(before,manifest(self.p,self.base));self.build()
        self.assertIn('用户采用（绑定当前内容）',self.gallery.read_text(encoding='utf-8'));self.assertIn('实际用户采用证据',self.gallery.read_text(encoding='utf-8'))
    def test_generated_outside_scope_is_current_outside_not_history(self):
        r=self.build();parser=self.parse();self.assertEqual(parser.outside,['outside']);self.assertNotIn('outside',parser.cards);self.assertEqual(parser.history,[]);self.assertEqual(r['assets'],7);self.assertEqual(r['outside_assets'],1)
        self.assertNotIn('outside',required_ids(self.p));verify(self.p,self.base,self.gallery)
    def test_same_id_sha_iterations_keep_every_record_without_duplicate_thumbnail(self):
        a=self.iteration('try1','mother');self.iteration('try2','mother',reason='保留退回理由：领口不对')
        self.build();parser=self.parse();self.assertEqual(parser.history,['try1','try2']);self.assertEqual(len(parser.actual_images),8)
        self.assertFalse(any(x['key'].startswith('history:') for x in parser.actual_images))
        self.assertEqual(load(self.gallery.parent/'全部迭代追溯.json'),self.p['iterations']);self.assertIn('领口不对',self.gallery.read_text(encoding='utf-8'))
        self.assertEqual(a['raw_request']['input_asset_id'],'mother');verify(self.p,self.base,self.gallery)
    def test_distinct_old_image_collapsed_under_its_asset_and_same_old_bytes_dedup(self):
        old=self.base/'old.png';Image.new('RGB',(64,36),'orange').save(old);self.iteration('old1','mother',old);self.iteration('old2','mother',old)
        original=old.read_bytes();self.build();parser=self.parse();self.assertEqual(len([x for x in parser.actual_images if x['key'].startswith('history:')]),1)
        doc=self.gallery.read_text(encoding='utf-8');self.assertLess(doc.index('id="asset-mother"'),doc.index('data-history-id="old1"'));self.assertLess(doc.index('data-history-id="old1"'),doc.index('id="asset-early"'))
        self.assertIn('<details class="history">',doc);self.assertEqual(old.read_bytes(),original);verify(self.p,self.base,self.gallery)
    def test_statuses_from_local_jobs_and_unknown_is_not_invented_failure(self):
        aids=[]
        for aid in ('ready','running','returned','failed','unknown','uncertain'):
            self.add(aid,aid,'prop');aids.append(aid)
        self.p['groups'][0]['bindings'].extend({'asset_id':x} for x in aids)
        self.jobs({'ready':{'phase':'ready'},'running':{'phase':'running','updated_at':'2026-10-21T10:00:00Z'},'returned':{'phase':'result_ready'},'failed':{'phase':'failed','error':'明确失败回执'},'uncertain':{'phase':'uncertain'}})
        before=(self.base/'production.sqlite').read_bytes();self.build();rows={a['id']:a for a in manifest(self.p,self.base)['cards']}
        self.assertEqual({x:rows[x]['progress']['status'] for x in aids},{'ready':'未开始','running':'生成中','returned':'返回待登记','failed':'失败','unknown':'待核实','uncertain':'待核实'})
        self.assertEqual(rows['running']['progress']['updated_at'],'2026-10-21T10:00:00Z');self.assertEqual(before,(self.base/'production.sqlite').read_bytes())
        self.assertEqual(rows['mother']['progress']['status'],'待审');verify(self.p,self.base,self.gallery)
    def test_no_database_or_network_or_approval_needed_for_build(self):
        self.p['settings']['asset_review_mode']=None
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network permitted')):
            self.assertEqual(self.build()['external_requests'],0);verify(self.p,self.base,self.gallery)
        self.assertFalse((self.base/'production.sqlite').exists());self.assertIn('待确认（未配置审核模式）',self.gallery.read_text(encoding='utf-8'))
        self.assertNotIn('image_approval',self.p['workflow'])
    def test_generation_status_does_not_become_current_ai_or_human_approval(self):
        a=self.asset('mother');a['generation_review_status']='待复核';a['review']={'status':'不通过','evidence':'AI观察领口不对'};self.iteration('first','mother')
        self.build();doc=self.gallery.read_text(encoding='utf-8');self.assertIn('生成时审核状态</dt><dd>待复核',doc);self.assertIn('AI当前结论</dt><dd>不通过',doc);self.assertIn('未记录用户采用',doc)
        self.assertEqual(self.p['iterations'][0]['status'],'待复核')
    def test_raw_request_receipt_and_unbound_asset_evidence_stay_clickable_unchanged(self):
        self.add('unstarted_outside','未开始范围外','prop')
        raw=self.base/'99_工作区'/'生图原始记录';raw.mkdir(parents=True)
        f=raw/'unstarted_outside_request_audit.json';save(f,{'prompt':'原始请求','audit':{'operation':'image_generate'}});original=f.read_bytes()
        self.build();snap=verify(self.p,self.base,self.gallery);self.assertTrue(any(t['asset_id']=='unstarted_outside' for t in snap['trace_files']));self.assertEqual(f.read_bytes(),original)
        self.assertIn('全部原始请求与回执索引',self.gallery.read_text(encoding='utf-8'))
    def test_visible_image_src_and_click_target_must_match_actual_file_even_if_html_resigned(self):
        self.build();doc=self.gallery.read_text(encoding='utf-8');target=self.parse().actual_images[0]['src']
        self.gallery.write_text(doc.replace('href="'+target+'"','href="missing.png"',1),encoding='utf-8');self.resign()
        with self.assertRaisesRegex(ValueError,'visible coverage'):verify(self.p,self.base,self.gallery)
    def test_current_duplicate_iteration_record_cannot_be_removed_from_visible_trace(self):
        self.iteration('same','mother');self.build();doc=self.gallery.read_text(encoding='utf-8').replace('data-history-id="same"','data-removed="same"');self.gallery.write_text(doc,encoding='utf-8');self.resign()
        with self.assertRaisesRegex(ValueError,'visible coverage'):verify(self.p,self.base,self.gallery)
    def test_registered_missing_image_cannot_pass_as_pending_placeholder(self):
        Path(self.asset('mother')['local_path']).unlink();self.build()
        with self.assertRaisesRegex(ValueError,'image missing'):verify(self.p,self.base,self.gallery)
    def test_same_hash_modified_real_bytes_cannot_pass(self):
        self.build();a=self.asset('mother');Image.new('RGB',(64,36),'cyan').save(a['local_path'])
        with self.assertRaisesRegex(ValueError,'hash changed'):verify(self.p,self.base,self.gallery)
    def test_refresh_keeps_content_digest_and_original_images(self):
        self.build();first=load(self.gallery.parent/SNAPSHOT);before={a['local_path']:Path(a['local_path']).read_bytes() for a in self.p['assets'] if a.get('local_path')};self.build();second=load(self.gallery.parent/SNAPSHOT)
        self.assertEqual(first['manifest_digest'],second['manifest_digest']);self.assertNotEqual(first['built_at'],second['built_at'])
        for path,data in before.items():self.assertEqual(Path(path).read_bytes(),data)
    def test_registration_is_idempotent_preserves_old_candidate_review_and_actual_edit_audit(self):
        self.build();old=self.asset('rain')['local_path'];old_hash=sha(old);f=self.base/'new-rain.png';Image.new('RGB',(64,36),'cyan').save(f)
        result={'phase':'saved','output':str(f),'sha256':sha(f),'audit':{'operation':'image_edit','input_asset_id':'mother','input_sha256':self.asset('mother')['sha256'],'input_count':1}}
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network permitted')):register_saved(self.pp,'rain',result)
        p=load(self.pp);a=next(a for a in p['assets'] if a['id']=='rain');a['review']={'status':'通过','evidence':'实际看图后记录'};save(self.pp,p);count=len(p['iterations']);register_saved(self.pp,'rain',result);p=load(self.pp)
        self.assertEqual(len(p['iterations']),count);self.assertEqual(next(a for a in p['assets'] if a['id']=='rain')['review']['status'],'通过');self.assertEqual(sha(old),old_hash)
        self.assertTrue(any(v['sha256']==old_hash for v in p['iterations']));self.assertEqual(next(a for a in p['assets'] if a['id']=='rain')['edit_audit'],result['audit']);verify(p,self.base,self.gallery)
    def test_saved_derivative_missing_actual_mother_audit_is_rejected_before_write(self):
        self.build();before=self.pp.read_bytes();f=self.base/'new.png';Image.new('RGB',(64,36),'cyan').save(f)
        with self.assertRaisesRegex(ValueError,'mother image audit'):register_saved(self.pp,'rain',{'phase':'saved','output':str(f),'sha256':sha(f),'audit':{'operation':'image_generate'}})
        self.assertEqual(before,self.pp.read_bytes())
    def test_empty_initial_template_builds_real_gallery_without_accepting_invalid_registered_scope(self):
        p={'project_id':'empty','version':'v1','episodes':[],'groups':[],'assets':[],'iterations':[],'settings':{},'workflow':{}}
        save(self.pp,p);self.assertEqual(build(self.pp)['assets'],0);verify(p,self.base,self.gallery);self.assertEqual(required_ids(p),set())
        p['workflow']['production_scope']={'episodes':[1]}
        with self.assertRaisesRegex(ValueError,'Production scope'):required_ids(p)
        p['episodes']=[{'id':1}];p['workflow']['production_scope']={'episodes':[2],'user_evidence':'实际错误范围'}
        with self.assertRaisesRegex(ValueError,'Production scope'):required_ids(p)
    def test_lineage_cycle_rejected_and_identical_names_never_used_as_fallback(self):
        self.asset('mother')['parent_id']='rain'
        with self.assertRaisesRegex(ValueError,'lineage cycle'):self.build()

if __name__=='__main__':unittest.main()
