"""Early previews, safe recovery and independently failed renderers, all offline."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import init_project as init

class InitialPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)/'剧集 用户 #1';self.base.mkdir()
        self.src=self.base/'原创剧本.txt';self.src.write_text('第一集\n阿明站在门口。',encoding='utf-8')
        self.paths=[self.base/folder/name for folder,name,_ in init.PANELS.values()]
    def tearDown(self):self.tmp.cleanup()
    def call(self,source=None):
        with patch('urllib.request.urlopen',side_effect=AssertionError('must be offline')):
            return init.initialize(self.base,source)
    def test_three_visible_before_source_read(self):
        real=init.extract
        def check(path):
            for p in self.paths:
                self.assertTrue(p.is_file());self.assertIn('准备中',p.read_text(encoding='utf-8'))
                self.assertIn('<a href=',p.read_text(encoding='utf-8'))
            return real(path)
        with patch.object(init,'extract',side_effect=check):out=self.call(self.src)
        self.assertEqual(out['status'],'complete');self.assertEqual(out['media_calls'],0)
    def test_directory_only_finds_single_source_and_keeps_original(self):
        before=self.src.read_bytes();out=self.call()
        self.assertEqual(out['status'],'complete');self.assertEqual(self.src.read_bytes(),before)
        self.assertEqual(Path(json.loads((self.base/'project.json').read_text(encoding='utf-8'))['input_source_path']),self.src.resolve())
        self.assertTrue(all(p.is_file() for p in self.paths))
    def test_directory_itself_also_autodetects(self):
        self.assertEqual(self.call(self.base)['status'],'complete')
    def test_multiple_sources_pause_without_guessing_then_resume(self):
        other=self.base/'另一个剧本.docx';other.write_text('dummy',encoding='utf-8')
        out=self.call();self.assertEqual(out['status'],'source_choice_pending');self.assertEqual(len(out['source_candidates']),2)
        for p in self.paths:self.assertIn('多个可能的原稿',p.read_text(encoding='utf-8'))
        self.assertFalse((self.base/'source.json').exists())
        out=self.call(self.src);self.assertEqual(out['status'],'complete');self.assertEqual(other.read_text(encoding='utf-8'),'dummy')
    def test_missing_source_preserves_previews_and_recovers(self):
        self.src.unlink();out=self.call();self.assertEqual(out['status'],'source_not_found')
        self.assertTrue(all('未找到原稿' in p.read_text(encoding='utf-8') for p in self.paths))
        self.src.write_text('第一集\n小雨推门。',encoding='utf-8');self.assertEqual(self.call()['status'],'complete')
    def test_bad_source_keeps_error_visible_and_retries(self):
        real=init.extract
        with patch.object(init,'extract',side_effect=ValueError('原创测试模拟解析失败')):
            out=self.call(self.src)
        self.assertEqual(out['status'],'source_failed')
        self.assertTrue(all('原稿提取失败' in p.read_text(encoding='utf-8') for p in self.paths))
        with patch.object(init,'extract',side_effect=real):out=self.call(self.src)
        self.assertEqual(out['status'],'complete')
    def test_unsupported_pdf_still_has_three_entrances(self):
        pdf=self.base/'unsupported.pdf';pdf.write_bytes(b'%PDF-fake')
        out=self.call(pdf);self.assertEqual(out['status'],'source_failed')
        self.assertTrue(all(p.is_file() and '原稿提取失败' in p.read_text(encoding='utf-8') for p in self.paths))
    def test_render_failures_are_independent_and_recover(self):
        for failed in init.PANELS:
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as d:
                target=Path(d)/'p';target.mkdir();source=target/'原稿.txt';source.write_text('第一集\n阿明开门。',encoding='utf-8')
                real=init._renderer
                def faulty(key,path):
                    if key==failed:raise RuntimeError('合成故障')
                    return real(key,path)
                with patch.object(init,'_renderer',side_effect=faulty):r=init.initialize(target,source)
                self.assertEqual(r['status'],'render_partial');self.assertTrue((target/init.PANELS[failed][0]/init.PANELS[failed][1]).is_file())
                self.assertIn('该入口更新失败',(target/init.PANELS[failed][0]/init.PANELS[failed][1]).read_text(encoding='utf-8'))
                self.assertTrue(all(k in r for k in init.PANELS if k!=failed))
                r=init.initialize(target,source);self.assertEqual(r['status'],'complete')
                self.assertFalse(any(p.name.startswith('.muflow-init-') for p in target.iterdir()))
    def test_existing_project_preserved(self):
        pp=self.base/'project.json';original=b'{"some":"old project"}';pp.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'Project exists'):self.call(self.src)
        self.assertEqual(pp.read_bytes(),original)
    def test_existing_source_json_preserved(self):
        p=self.base/'source.json';raw=b'{"other":"keep"}';p.write_bytes(raw)
        out=self.call(self.src);self.assertEqual(out['status'],'source_failed');self.assertEqual(p.read_bytes(),raw)
        self.assertTrue(all(x.is_file() for x in self.paths))
    def test_existing_panel_not_overwritten_other_panels_update(self):
        p=self.paths[0];p.parent.mkdir();p.write_text('用户已有页面',encoding='utf-8')
        out=self.call(self.src);self.assertEqual(out['status'],'render_partial');self.assertEqual(p.read_text(encoding='utf-8'),'用户已有页面')
        self.assertIn('preview',out);self.assertIn('videos',out)
    def test_unrelated_existing_file_is_not_overwritten(self):
        existing=self.base/'03_资产图'/'用户旧素材.png';existing.parent.mkdir();existing.write_bytes(b'user file')
        out=self.call(self.src);self.assertEqual(out['status'],'complete');self.assertEqual(existing.read_bytes(),b'user file')
    def test_existing_output_sidecar_is_not_overwritten(self):
        existing=self.base/'03_资产图'/'当前候选图片清单.json';existing.parent.mkdir();existing.write_text('{"old":true}',encoding='utf-8')
        out=self.call(self.src);self.assertEqual(out['status'],'render_partial');self.assertEqual(existing.read_text(encoding='utf-8'),'{"old":true}')
        self.assertIn('更新失败',self.paths[0].read_text(encoding='utf-8'));self.assertIn('preview',out);self.assertIn('videos',out)
    def test_modified_placeholder_is_not_overwritten_on_retry(self):
        with patch.object(init,'extract',side_effect=ValueError('首次失败')):self.call(self.src)
        self.paths[1].write_text('用户自己编辑的内容',encoding='utf-8')
        out=self.call(self.src);self.assertEqual(out['status'],'render_partial');self.assertEqual(self.paths[1].read_text(encoding='utf-8'),'用户自己编辑的内容')
        self.assertIn('assets',out);self.assertIn('videos',out)
    def test_changed_original_after_extract_is_not_silently_accepted(self):
        real=init._renderer
        def faulty(key,path):
            if key=='assets':raise ValueError('首次渲染失败')
            return real(key,path)
        with patch.object(init,'_renderer',side_effect=faulty):out=self.call(self.src)
        old=(self.base/'source.json').read_bytes();self.src.write_text('改变的原稿',encoding='utf-8')
        out=self.call(self.src);self.assertEqual(out['status'],'source_failed');self.assertEqual((self.base/'source.json').read_bytes(),old)

    def test_preview_snapshot_matches_final_project_digest(self):
        from core import digest
        self.assertEqual(self.call(self.src)['status'],'complete')
        project=json.loads((self.base/'project.json').read_text(encoding='utf-8'))
        checks=json.loads((self.base/'07_跑前预览'/'preview_checks.json').read_text(encoding='utf-8'))
        self.assertEqual(checks['source_digest'],digest(project))
    def test_resume_after_interrupted_owned_write(self):
        with patch.object(init,'extract',side_effect=ValueError('preparing')):self.call(self.src)
        pp=self.base/'project.json';project=json.loads(pp.read_text(encoding='utf-8'));meta=project[init.STATE]
        state_path=self.base/meta['state_path'];state=json.loads(state_path.read_text(encoding='utf-8'))
        path=self.paths[0];rel='03_资产图/00_资产参考图总览.html';before=state['owned_files'][rel]
        new=b'<!-- MUFLOW_INIT_V240_PLACEHOLDER -->interrupted-owned-content';state['pending_files'][rel]={'before':before,'after':init._hash(new)}
        state_path.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');path.write_bytes(new)
        out=self.call(self.src);self.assertEqual(out['status'],'complete');self.assertNotEqual(path.read_bytes(),new)
    def test_changed_file_during_pending_write_is_preserved(self):
        with patch.object(init,'extract',side_effect=ValueError('preparing')):self.call(self.src)
        project=json.loads((self.base/'project.json').read_text(encoding='utf-8'));state_path=self.base/project[init.STATE]['state_path']
        state=json.loads(state_path.read_text(encoding='utf-8'));rel='03_资产图/00_资产参考图总览.html'
        state['pending_files'][rel]={'before':state['owned_files'][rel],'after':init._hash(b'our expected write')}
        state_path.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');self.paths[0].write_text('用户修改',encoding='utf-8')
        out=self.call(self.src);self.assertEqual(out['status'],'render_partial');self.assertEqual(self.paths[0].read_text(encoding='utf-8'),'用户修改')

    def test_published_html_links_resolve_inside_actual_project(self):
        from html.parser import HTMLParser
        from urllib.parse import urljoin,urlsplit
        from urllib.request import url2pathname
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.urls=[]
            def handle_starttag(self,tag,attrs):
                values=dict(attrs)
                for key in ('href','src'):
                    if key in values:self.urls.append(values[key])
        out=self.call(self.src);self.assertEqual(out['status'],'complete');observed=0
        for panel in self.paths:
            content=panel.read_text(encoding='utf-8');self.assertNotIn('.muflow-init-',content)
            parser=Links();parser.feed(content)
            for url in parser.urls:
                if not url or url.startswith('#'):continue
                parsed=urlsplit(urljoin(panel.as_uri(),url))
                if parsed.scheme!='file':continue
                target=Path(url2pathname(parsed.path))
                self.assertTrue(target.is_file(),f'{panel.name} points to missing {target}')
                self.assertTrue(target.resolve().is_relative_to(self.base.resolve()),f'link escaped project: {target}')
                observed+=1
        self.assertGreaterEqual(observed,8)
        for folder in ('03_资产图','07_跑前预览','08_视频'):
            for path in (self.base/folder).rglob('*'):
                if path.is_file() and path.suffix.lower() in ('.html','.json','.js','.md','.txt'):
                    self.assertNotIn('.muflow-init-',path.read_text(encoding='utf-8'),str(path))

    def test_initial_preview_waits_for_shared_refresh_lock(self):
        from batch_video import blocking_exclusive
        import threading,time
        results=[];errors=[]
        def launch():
            try:results.append(self.call(self.src))
            except Exception as exc:errors.append(exc)
        with blocking_exclusive(self.base/'.preview-refresh.lock'):
            thread=threading.Thread(target=launch);thread.start()
            time.sleep(.15)
            self.assertTrue(thread.is_alive())
            self.assertFalse(any(p.exists() for p in self.paths))
        thread.join(10)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(results[0]['status'],'complete')

    def test_actual_initial_gallery_passes_manifest_and_hash_verification(self):
        from asset_gallery import verify,GALLERY
        from core import load
        out=self.call(self.src);self.assertEqual(out['status'],'complete')
        project=load(self.base/'project.json');snapshot=verify(project,self.base,'03_资产图/'+GALLERY)
        self.assertEqual(snapshot['panel_sha256'],init._hash(self.paths[0].read_bytes()))
        extracted=load(self.base/'source.json')
        self.assertEqual(extracted['sha256'],init._hash(self.src.read_bytes()))
        self.assertEqual(project['source_path'],'source.json')

    def _project_with_real_relative_asset(self):
        from core import load,save,sha
        from PIL import Image
        self.assertEqual(self.call(self.src)['status'],'complete')
        images=self.base/'原始参考';images.mkdir();img=images/'角色.png';Image.new('RGB',(40,20),'blue').save(img)
        prompts=self.base/'提示词';prompts.mkdir();(prompts/'G01.txt').write_text('原创测试提示词',encoding='utf-8')
        p=load(self.base/'project.json');p['assets']=[{'id':'A1','name':'原创角色','kind':'role','mode':'reuse','local_path':'原始参考/角色.png','sha256':sha(img),'prompt':'已提供参考'}]
        p['episodes']=[{'id':1,'title':'原创第一集','ending':'待续'}]
        p['groups']=[{'id':'G01','episode':1,'expected_seconds':30,'prompt_path':'提示词/G01.txt','bindings':[{'name':'原创角色','asset_id':'A1'}],
                      'shots':[{'id':'S01','seconds':30,'camera':'中景','action':'站立','speech':[],'asset_ids':['A1']}]}]
        save(self.base/'project.json',p);return p,img
    def test_owned_refresh_real_relative_asset_and_snapshot(self):
        from core import load,digest,sha
        from asset_gallery import verify,GALLERY
        p,img=self._project_with_real_relative_asset();before=img.read_bytes()
        out=init.refresh_owned(self.base/'project.json');self.assertFalse(out['issues'],out['issues'])
        snap=verify(p,self.base,'03_资产图/'+GALLERY)
        self.assertEqual(snap['panel_sha256'],sha(self.paths[0]));self.assertEqual(img.read_bytes(),before)
        self.assertTrue(snap['visible_images']);self.assertTrue((self.paths[0].parent/snap['visible_images'][0]['file']).is_file())
        self.assertEqual(load(self.base/'07_跑前预览/preview_checks.json')['source_digest'],digest(p))
        html=self.paths[1].read_text(encoding='utf-8');self.assertIn('preview_resources/',html)
        self.assertTrue(any((self.base/'07_跑前预览/preview_resources').iterdir()))
        meta=p[init.STATE];state=load(self.base/meta['state_path'])
        self.assertEqual(state['owned_files']['03_资产图/00_资产参考图总览.html'],sha(self.paths[0]))
    def test_owned_refresh_preserves_tampered_sidecar(self):
        p,img=self._project_with_real_relative_asset();self.assertFalse(init.refresh_owned(self.base/'project.json')['issues'])
        target=self.base/'03_资产图/实际资产提示词.md';target.write_text('用户修改，不许覆盖',encoding='utf-8')
        out=init.refresh_owned(self.base/'project.json')
        self.assertTrue(any(x['object']=='assets' for x in out['issues']));self.assertEqual(target.read_text(encoding='utf-8'),'用户修改，不许覆盖')
        self.assertIn('preview',out);self.assertIn('videos',out)
    def test_owned_refresh_blocks_unowned_thumbnail(self):
        from core import sha
        p,img=self._project_with_real_relative_asset();folder=self.base/'07_跑前预览/preview_resources';folder.mkdir(parents=True)
        target=folder/(sha(img)+'-thumb.jpg');target.write_bytes(b'user-owned')
        out=init.refresh_owned(self.base/'project.json')
        self.assertTrue(any(x['object']=='preview' for x in out['issues']));self.assertEqual(target.read_bytes(),b'user-owned')
        self.assertIn('assets',out);self.assertIn('videos',out)
    def test_owned_refresh_never_adopts_untracked_existing_project(self):
        from core import save
        save(self.base/'project.json',{'project_id':'unrelated'})
        user=self.base/'03_资产图/00_资产参考图总览.html';user.parent.mkdir();user.write_text('用户文件',encoding='utf-8')
        out=init.refresh_owned(self.base/'project.json');self.assertTrue(out['issues']);self.assertEqual(user.read_text(encoding='utf-8'),'用户文件')
    def test_owned_refresh_blocks_interrupted_prior_refresh(self):
        from core import load,save
        self.assertEqual(self.call(self.src)['status'],'complete');p=load(self.base/'project.json');path=self.base/p[init.STATE]['state_path']
        state=load(path);state['refresh_in_progress']={'panel':'assets'};save(path,state);before=[x.read_bytes() for x in self.paths]
        out=init.refresh_owned(self.base/'project.json');self.assertTrue(out['issues']);self.assertEqual([x.read_bytes() for x in self.paths],before)

    def test_owned_refresh_preserves_video_shell_with_same_and_new_data(self):
        import sqlite3,hashlib
        from core import load,save,sha
        self.assertEqual(self.call(self.src)['status'],'complete')
        panel=self.paths[2];first=panel.read_bytes();mtime=panel.stat().st_mtime_ns
        statefile=self.base/load(self.base/'project.json')[init.STATE]['state_path']
        self.assertFalse(init.refresh_owned(self.base/'project.json')['issues'])
        self.assertEqual(panel.read_bytes(),first);self.assertEqual(panel.stat().st_mtime_ns,mtime)
        self.assertEqual(load(statefile)['owned_files']['08_视频/已出成片预览.html'],sha(panel))
        # Synthetic nonempty file checks queue/state only, not video decoding.
        clip=self.base/'08_视频/模拟片段.mp4';clip.write_bytes(b'synthetic-local-file')
        p=load(self.base/'project.json');p['episodes']=[{'id':1,'title':'原创'}]
        p['groups']=[{'id':'G01','episode':1,'expected_seconds':30,'shots':[],'bindings':[]}];save(self.base/'project.json',p)
        conn=sqlite3.connect(self.base/'production.sqlite')
        try:
            conn.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT)')
            conn.execute('INSERT INTO jobs VALUES (?,?)',('G01',json.dumps({'operation':'video','phase':'downloaded','output':'08_视频/模拟片段.mp4','bytes':clip.stat().st_size,'sha256':hashlib.sha256(clip.read_bytes()).hexdigest()})))
            conn.commit()
        finally:conn.close()
        before_data=(self.base/'08_视频/成片进度.js').read_bytes()
        out=init.refresh_owned(self.base/'project.json');self.assertFalse(out['issues'],out['issues'])
        self.assertEqual(panel.read_bytes(),first);self.assertEqual(panel.stat().st_mtime_ns,mtime)
        self.assertNotEqual((self.base/'08_视频/成片进度.js').read_bytes(),before_data)
        self.assertEqual(load(self.base/'08_视频/成片清单.json')['downloaded'],1)
        owned=load(statefile)['owned_files'];self.assertEqual(owned['08_视频/成片进度.js'],sha(self.base/'08_视频/成片进度.js'));self.assertEqual(owned['08_视频/已出成片预览.html'],sha(panel))

    def test_actual_image_registration_uses_owned_refresh(self):
        from core import load,save,sha
        from asset_gallery import register_saved,verify,GALLERY
        from PIL import Image
        self.assertEqual(self.call(self.src)['status'],'complete')
        p=load(self.base/'project.json');p['assets']=[{'id':'A1','name':'原创角色','kind':'role','mode':'generate','prompt':'原创角色'}];save(self.base/'project.json',p)
        folder=self.base/'已返回图片';folder.mkdir();img=folder/'角色.png';Image.new('RGB',(40,20),'blue').save(img);before=img.read_bytes()
        result=register_saved(self.base/'project.json','A1',{'phase':'saved','output':str(img),'sha256':sha(img)})
        self.assertFalse(result['issues'],result['issues']);self.assertEqual(img.read_bytes(),before)
        current=load(self.base/'project.json');snap=verify(current,self.base,'03_资产图/'+GALLERY)
        self.assertTrue(snap['visible_images']);self.assertIn('原创角色',self.paths[0].read_text(encoding='utf-8'))
    def test_stage_completion_refreshes_actual_previews(self):
        from core import load,sha,digest
        from asset_gallery import verify,GALLERY
        from stage_queue import next_batch,start_batch,complete_batch
        self.src.write_text('\n'.join('第'+str(i)+'集\n原创事件'+str(i) for i in range(1,7)),encoding='utf-8')
        self.assertEqual(self.call(self.src)['status'],'complete');pp=self.base/'project.json'
        first=next_batch(pp);self.assertEqual(first['episodes'],[1,2,3,4,5]);start_batch(pp,first['batch_id'])
        artifact=self.base/'原创前五集剧本.md';artifact.write_text('\n'.join('第'+str(i)+'集\n合成正文' for i in range(1,6)),encoding='utf-8')
        complete_batch(pp,first['batch_id'],[{'path':str(artifact),'sha256':sha(artifact),'kind':'screenplay','episodes':[1,2,3,4,5]}],'合成前五集文件已核对')
        record=self.base/'99_工作区/阶段队列_面板刷新记录.jsonl';events=[json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(events);self.assertTrue(all(e['status']=='通过' for e in events),events)
        nxt=next_batch(pp);self.assertEqual(nxt['episodes'],[6])
        p=load(pp);verify(p,self.base,'03_资产图/'+GALLERY)
        self.assertEqual(load(self.base/'07_跑前预览/preview_checks.json')['source_digest'],digest(p))
        self.assertIn('SC002',(self.base/'07_跑前预览/全剧跑前预览.html').read_text(encoding='utf-8'))

    def test_owned_refresh_writes_audit_report_and_progress_status(self):
        from core import load,sha
        self.assertEqual(self.call(self.src)['status'],'complete')
        out=init.refresh_owned(self.base/'project.json');self.assertFalse(out['issues'],out['issues'])
        report=self.base/'03_资产图/资产审核问题汇总.md';status=self.base/'99_工作区/面板刷新状态.json'
        self.assertEqual(Path(out['review_report']),report);self.assertEqual(Path(out['status_file']),status)
        self.assertTrue(report.is_file());self.assertEqual(load(status),out)
        p=load(self.base/'project.json');state=load(self.base/p[init.STATE]['state_path'])
        self.assertEqual(state['owned_files']['03_资产图/资产审核问题汇总.md'],sha(report))
        self.assertEqual(state['owned_files']['99_工作区/面板刷新状态.json'],sha(status))
        self.assertFalse(init.refresh_owned(self.base/'project.json')['issues'])
    def test_owned_refresh_does_not_overwrite_foreign_review_report(self):
        from core import load
        self.assertEqual(self.call(self.src)['status'],'complete')
        report=self.base/'03_资产图/资产审核问题汇总.md';report.write_text('用户已有审核报告',encoding='utf-8')
        out=init.refresh_owned(self.base/'project.json')
        self.assertTrue(any(x['object']=='review_report' for x in out['issues']))
        self.assertEqual(report.read_text(encoding='utf-8'),'用户已有审核报告');self.assertNotIn('review_report',out)
        self.assertTrue(any(x['object']=='review_report' for x in load(out['status_file'])['issues']))
    def test_owned_refresh_does_not_overwrite_foreign_status_file(self):
        self.assertEqual(self.call(self.src)['status'],'complete')
        status=self.base/'99_工作区/面板刷新状态.json';status.write_text('{"user":"keep"}',encoding='utf-8')
        out=init.refresh_owned(self.base/'project.json')
        self.assertTrue(any(x['object']=='refresh_status' for x in out['issues']))
        self.assertEqual(status.read_text(encoding='utf-8'),'{"user":"keep"}');self.assertNotIn('status_file',out)
        self.assertTrue(Path(out['review_report']).is_file())

if __name__=='__main__':unittest.main()
