"""Full-series local batches, independent coverage, real evidence and restart behavior."""
import contextlib,copy,io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from PIL import Image
from core import load,save,sha
from stage_queue import initialize,next_batch,start_batch,complete_batch,set_status,retry_batch,progress,QUEUE

class StageQueue(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.pp=self.base/'project.json'
        source=self.base/'source.txt';source.write_text('\n'.join('第'+str(i)+'集\n原创台词与事件 '+str(i) for i in range(1,27)),encoding='utf-8')
        self.p={'project_id':'local-stage-test','source_path':str(source),'version':'v2.3.0','episodes':[{'id':i} for i in range(1,6)],'groups':[],
            'settings':{'asset_review_mode':'agent'},'assets':[],'workflow':{'production_scope':{'episodes':[1,2,3,4,5],'user_evidence':'仅本次查看前5集；不得缩全剧'}}}
        self.sync()
    def tearDown(self):self.tmp.cleanup()
    def sync(self):save(self.pp,self.p)
    def artifact(self,batch,kind,episodes=None,asset_id=None):
        f=self.base/(batch['id']+'_'+kind+('_'+asset_id if asset_id else '')+('.png' if kind=='image' else '.md'))
        if kind=='image':Image.new('RGB',(64,36),'green').save(f)
        else:f.write_text('实际离线产物 '+kind+'\n'+'\n'.join('第'+str(e)+'集\n对应剧本内容' for e in (episodes or batch.get('episodes',[]))),encoding='utf-8')
        row={'path':str(f),'sha256':sha(f),'kind':kind}
        if asset_id:row['asset_id']=asset_id
        else:row['episodes']=episodes if episodes is not None else batch['episodes']
        return row
    def finish(self,ready):
        start_batch(self.pp,ready['batch_id']);kinds=['screenplay'] if ready['stage']=='screenplay' else ['storyboard','asset_table']
        artifacts=[self.artifact({'id':ready['batch_id'],'episodes':ready['episodes']},k) for k in kinds]
        return complete_batch(self.pp,ready['batch_id'],artifacts,'实际产物已保存并按集核对')
    def finish_text_stages(self):
        while True:
            r=next_batch(self.pp)
            if r['stage'] not in ('screenplay','storyboard_assets'):return r
            self.finish(r)
    def add_image(self,aid,parent=None,qualified=False):
        a={'id':aid,'name':aid,'kind':'role','mode':'edit' if parent else 'generate','parent_id':parent,'prompt':'原创角色参考 '+aid,'entity_id':'hero','state_key':aid,'review':{'status':'待复核'}}
        if qualified:
            f=self.base/(aid+'.png');Image.new('RGB',(64,36),'blue').save(f);a.update(local_path=str(f),sha256=sha(f));self.review(a)
        self.p['assets'].append(a);self.sync();return a
    def review(self,a):
        from review_state import asset_subject
        a['review']={'status':'通过','subject_digest':asset_subject(self.p,a),'performed_by':'agent','checked_at':'2026-10-21T12:00:00Z','evidence':'离线合成图实际打开检查',
            'checks':[{'rule_id':r,'expected':'原创设计参考一致','observed':'已打开本地图片核对该项','evidence':'实际文件 '+str(a.get('local_path')),'result':'通过'} for r in ('identity_stage','appearance')]}
    def image_outputs(self,r,review_parent=False):
        start_batch(self.pp,r['batch_id']);rows=[];assets={a['id']:a for a in self.p['assets']}
        for aid in r['asset_ids']:
            row=self.artifact({'id':r['batch_id']},'image',asset_id=aid);a=assets[aid];a.update(local_path=row['path'],sha256=row['sha256'])
            if a.get('parent_id'):
                parent=assets[a['parent_id']];a['edit_audit']={'operation':'image_edit','input_asset_id':parent['id'],'input_sha256':parent['sha256'],'input_count':1}
            if review_parent:self.review(a)
            rows.append(row)
        self.sync();complete_batch(self.pp,r['batch_id'],rows,'逐张实际文件哈希与生成回执登记');return rows
    def test_independent_26_episode_scope_ignores_first_five_registration_and_scope(self):
        q=initialize(self.pp);self.assertEqual(q['full_episode_ids'],list(range(1,27)))
        self.assertEqual([b['episodes'] for b in q['stages']['screenplay']['batches']],[[1,2,3,4,5],[6,7,8,9,10],[11,12,13,14,15],[16,17,18,19,20],[21,22,23,24,25],[26]])
        self.assertEqual(len(q['stages']['storyboard_assets']['batches']),6);self.assertEqual(progress(self.p,self.base)['source_episodes'],list(range(1,27)))
    def test_first_five_complete_automatically_exposes_next_five_not_downstream(self):
        r=next_batch(self.pp);self.finish(r);n=next_batch(self.pp)
        self.assertEqual(n['stage'],'screenplay');self.assertEqual(n['episodes'],[6,7,8,9,10]);self.assertEqual(load(self.base/QUEUE)['cursor']['batch_id'],n['batch_id'])
        with self.assertRaisesRegex(ValueError,'next ready'):start_batch(self.pp,'SA001')
    def test_all_screenplays_before_any_storyboard_then_all_storyboards_before_images(self):
        for i in range(6):
            r=next_batch(self.pp);self.assertEqual(r['stage'],'screenplay');self.finish(r)
        self.assertEqual(next_batch(self.pp)['batch_id'],'SA001')
        self.assertEqual(self.finish_text_stages()['stage'],'images');self.assertEqual(next_batch(self.pp)['action'],'blocked')
    def test_unknown_novel_chapters_are_not_silently_five_episodes(self):
        Path(self.p['source_path']).write_text('第一章 原创小说正文\n第二章 后续',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'independently verified'):initialize(self.pp)
        self.assertFalse((self.base/QUEUE).exists())
    def test_confirmed_episode_baseline_binds_novel_hash(self):
        source=Path(self.p['source_path']);source.write_text('第一章 原创小说正文',encoding='utf-8');self.p['episode_baseline']={'confirmed':True,'episode_ids':list(range(1,12)),'source_sha256':sha(source),'evidence':'全篇改编分集完成的独立基准'};self.sync()
        self.assertEqual(initialize(self.pp)['full_episode_ids'],list(range(1,12)))
    def test_completed_proof_is_nonempty_hash_bound_and_covers_every_episode_and_kind(self):
        r=next_batch(self.pp);start_batch(self.pp,r['batch_id'])
        for records in ([],[{'path':str(self.base/'none.md'),'sha256':'0'*64,'kind':'screenplay','episodes':r['episodes']}]):
            with self.assertRaises(ValueError):complete_batch(self.pp,r['batch_id'],records,'已完成')
        row=self.artifact({'id':r['batch_id'],'episodes':r['episodes']},'screenplay',episodes=[1,2])
        with self.assertRaisesRegex(ValueError,'every episode'):complete_batch(self.pp,r['batch_id'],[row],'只产出两集')
        f=self.base/'empty.md';f.write_bytes(b'')
        with self.assertRaisesRegex(ValueError,'nonempty'):complete_batch(self.pp,r['batch_id'],[{'path':str(f),'sha256':sha(f),'kind':'screenplay','episodes':r['episodes']}],'空文件不算')
    def test_storyboard_batch_needs_both_storyboard_and_asset_table(self):
        for i in range(6):self.finish(next_batch(self.pp))
        r=next_batch(self.pp);start_batch(self.pp,r['batch_id']);row=self.artifact({'id':r['batch_id'],'episodes':r['episodes']},'storyboard')
        with self.assertRaisesRegex(ValueError,'asset_table'):complete_batch(self.pp,r['batch_id'],[row],'缺资产表')
    def test_restart_idempotent_completion_keeps_cursor_and_old_proof(self):
        r=next_batch(self.pp);b=self.finish(r);before=(self.base/QUEUE).read_bytes();initialize(self.pp)
        complete_batch(self.pp,r['batch_id'],b['artifacts'],'实际产物已保存并按集核对');self.assertEqual(before,(self.base/QUEUE).read_bytes())
        nxt=next_batch(self.pp);again=next_batch(self.pp);self.assertEqual(nxt,again);self.assertEqual(progress(self.pp)['cursor']['batch_id'],nxt['batch_id'])
    def test_failed_batch_requires_explicit_local_retry_without_erasing_failure(self):
        r=next_batch(self.pp);start_batch(self.pp,r['batch_id']);set_status(self.pp,r['batch_id'],'failed','实际工具返回失败证据')
        self.assertEqual(next_batch(self.pp)['action'],'failed');retry_batch(self.pp,r['batch_id'],'已核对原失败，准备恢复本批');self.assertEqual(next_batch(self.pp)['action'],'ready')
        q=load(self.base/QUEUE);self.assertTrue(any(e['action']=='failed' for e in q['events']));self.assertEqual(q['stages']['screenplay']['batches'][0]['history'][-1]['evidence'],'已核对原失败，准备恢复本批')
    def test_changed_source_or_shrunken_scope_cannot_reset_prior_full_plan(self):
        initialize(self.pp);self.p['workflow']['production_scope']['episodes']=[1];self.sync();self.assertEqual(initialize(self.pp)['full_episode_ids'],list(range(1,27)))
        Path(self.p['source_path']).write_text('第1集\n被缩短',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'never silently shrink'):initialize(self.pp)
        self.assertEqual(progress(self.pp)['state'],'blocked')
    def test_changed_completed_artifact_blocks_next_stage_and_preserves_proof(self):
        b=self.finish(next_batch(self.pp));Path(b['artifacts'][0]['path']).write_text('修改后的内容',encoding='utf-8')
        r=next_batch(self.pp);self.assertEqual(r['action'],'blocked');old=load(self.base/QUEUE)['stages']['screenplay']['batches'][0]
        self.assertEqual(old['completion']['evidence'],'实际产物已保存并按集核对');self.assertEqual(old['status'],'blocked')
    def test_image_batches_max_twenty_and_mother_completes_before_child(self):
        for i in range(21):self.add_image('root'+str(i))
        self.add_image('child','root0');r=self.finish_text_stages();self.assertEqual(len(r['asset_ids']),20);self.assertNotIn('child',r['asset_ids'])
        self.image_outputs(r,review_parent=True);r2=next_batch(self.pp);self.assertEqual(set(r2['asset_ids']),{'root20','child'});self.assertLessEqual(len(r2['asset_ids']),20)
        self.image_outputs(r2);final=next_batch(self.pp);self.assertEqual(final['action'],'awaiting_review');self.assertEqual(final['stage'],'asset_review');self.assertFalse(final['video_authorized']);self.assertFalse(final['generation_authorized'])
    def test_child_waits_for_actual_mother_review_not_just_saved_file(self):
        self.add_image('mother');self.add_image('child','mother');r=self.finish_text_stages();self.assertEqual(r['asset_ids'],['mother']);self.image_outputs(r)
        n=next_batch(self.pp);self.assertEqual(n['action'],'blocked');self.assertIn('母图',n['reason'])
    def test_qualified_existing_image_reused_without_new_image_batch_or_network(self):
        self.add_image('reuse',qualified=True)
        with patch('urllib.request.urlopen',side_effect=AssertionError('No API permitted')):r=self.finish_text_stages()
        self.assertEqual(r['action'],'awaiting_review');self.assertFalse(r['full_structure_complete']);q=load(self.base/QUEUE);self.assertEqual(q['stages']['images']['batches'],[]);self.assertEqual(q['stages']['images']['items']['reuse']['completion']['kind'],'qualified_reuse')
    def test_two_real_accumulating_exports_preserve_prior_batch_evidence(self):
        from export_assets import export
        self.p['settings'].update(medium='真人',quality='自然摄影原创测试',scene_ratio='16:9')
        self.add_image('hero')
        for i in range(6):self.finish(next_batch(self.pp))
        tables=self.base/'02_资产表';data={'表1 核心角色卡':{'rows':[{'编号':'hero','姓名':'原创主角','生图提示词':'原创主角基线参考'}]}}
        with contextlib.redirect_stdout(io.StringIO()):export(data,self.p,tables)
        fixed=tables/'资产拆解表.xlsx';first_hash=sha(fixed)
        r=next_batch(self.pp);start_batch(self.pp,r['batch_id']);story=self.artifact({'id':r['batch_id'],'episodes':r['episodes']},'storyboard')
        first_proof=[story,{'path':str(fixed),'sha256':first_hash,'kind':'asset_table','episodes':r['episodes']}]
        first=complete_batch(self.pp,r['batch_id'],first_proof,'第1至5集累计表实际导出核对');snap=next(x for x in first['artifacts'] if x['kind']=='asset_table')
        self.assertTrue(snap['evidence_snapshot']);self.assertNotEqual(snap['path'],str(fixed));self.assertEqual(sha(self.base/snap['path']),first_hash)
        self.add_image('later');data['表1 核心角色卡']['rows'].append({'编号':'later','姓名':'后续角色','生图提示词':'后续原创角色参考'})
        with contextlib.redirect_stdout(io.StringIO()):export(data,self.p,tables,refresh=True)
        self.assertNotEqual(sha(fixed),first_hash);self.assertEqual(sha(self.base/snap['path']),first_hash)
        r2=next_batch(self.pp);self.assertEqual(r2['batch_id'],'SA002');self.assertEqual(r2['action'],'ready');start_batch(self.pp,r2['batch_id'])
        story2=self.artifact({'id':r2['batch_id'],'episodes':r2['episodes']},'storyboard')
        complete_batch(self.pp,r2['batch_id'],[story2,{'path':str(fixed),'sha256':sha(fixed),'kind':'asset_table','episodes':r2['episodes']}],'第6至10集累计表实际导出核对')
        self.assertEqual(next_batch(self.pp)['batch_id'],'SA003');self.assertEqual(complete_batch(self.pp,first['id'],first_proof,'第1至5集累计表实际导出核对')['artifacts'],first['artifacts'])
        self.assertTrue(list((tables/'history').glob('*/资产拆解表.xlsx')))
    def test_given_locked_identity_is_reused_for_derivation_before_final_adoption(self):
        mother=self.add_image('locked');self.add_image('derived','locked');mother['mode']='reuse';self.p['settings']['asset_review_mode']='joint'
        f=self.base/'provided-identity.png';Image.new('RGB',(64,36),'orange').save(f);mother.update(local_path=str(f),sha256=sha(f))
        self.p['input_assets']=[{'asset_id':'locked','entity_id':'hero','local_path':str(f),'sha256':sha(f),'identity_locked':True}];self.sync()
        with patch('urllib.request.urlopen',side_effect=AssertionError('No API permitted')):r=self.finish_text_stages()
        self.assertEqual(r['action'],'ready');self.assertEqual(r['asset_ids'],['derived']);q=load(self.base/QUEUE);item=q['stages']['images']['items']['locked']
        self.assertEqual(item['status'],'completed');self.assertEqual(item['completion']['kind'],'provided_identity_reuse');self.assertNotIn('image_approval',self.p['workflow'])
        from review_state import asset_ready
        with self.assertRaises(ValueError):asset_ready(self.p,mother)
    def test_unreviewed_reuse_asset_blocks_instead_of_becoming_new_generation(self):
        a=self.add_image('existing');a['mode']='reuse';self.sync();r=self.finish_text_stages()
        self.assertEqual(r['action'],'blocked');self.assertIn('不重新生图',r['reason'])
    def test_changed_current_candidate_invalidates_old_completed_image_and_preserves_original_proof(self):
        self.add_image('generated');r=self.finish_text_stages();rows=self.image_outputs(r);old=load(self.base/QUEUE)['stages']['images']['batches'][0]
        self.assertTrue(old['completion_history']);fresh=self.base/'fresh.png';Image.new('RGB',(64,36),'yellow').save(fresh)
        self.p['assets'][0].update(local_path=str(fresh),sha256=sha(fresh));self.sync();view=progress(self.pp);self.assertEqual(view['state'],'blocked')
        n=next_batch(self.pp);self.assertEqual(n['action'],'blocked');b=load(self.base/QUEUE)['stages']['images']['batches'][0];self.assertEqual(b['status'],'blocked');self.assertEqual(b['completion_history'][0]['artifacts'],rows)
    def test_final_boundary_reads_shared_readiness_and_never_authorizes_video(self):
        self.add_image('reuse',qualified=True)
        with patch('review_state.readiness',return_value={'label':'半血','reviews_complete':False,'source_full_complete':True,'evidence':[]}):
            r=self.finish_text_stages();self.assertEqual(r['action'],'awaiting_review');self.assertEqual(progress(self.pp)['state'],'awaiting_review')
        with patch('review_state.readiness',return_value={'label':'满血','reviews_complete':True,'source_full_complete':True,'evidence':[]}):
            r=next_batch(self.pp);self.assertEqual(r['action'],'ready_preview');self.assertEqual(progress(self.pp)['state'],'ready_preview');self.assertFalse(r['generation_authorized']);self.assertFalse(r['video_authorized'])
        self.assertFalse(load(self.base/QUEUE)['video_authorized'])
    def test_asset_updates_append_without_silent_removal(self):
        self.add_image('first');initialize(self.pp);self.add_image('second');self.assertEqual(set(initialize(self.pp)['stages']['images']['items']),{'first','second'})
        self.p['assets']=self.p['assets'][1:];self.sync()
        with self.assertRaisesRegex(ValueError,'shrink'):initialize(self.pp)
    def test_progress_is_read_only_and_missing_base_does_not_guess_cwd(self):
        self.assertEqual(progress(self.p)['state'],'not_initialized');self.assertFalse((self.base/QUEUE).exists());initialize(self.pp);before=(self.base/QUEUE).read_bytes();view=progress(self.p,self.base)
        self.assertEqual(view['stage'],'screenplay');self.assertEqual(before,(self.base/QUEUE).read_bytes());self.assertFalse(view['video_authorized'])

if __name__=='__main__':unittest.main()
