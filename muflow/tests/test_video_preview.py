"""Video file/HTTP/DOM behavior tests; no media generation or content review."""
import contextlib,json,os,shutil,subprocess,sys,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import load,save,sha
from video_gallery import build,read_tasks,PANEL,DATA,MANIFEST,callback
from serve_video_preview import create_server,byte_range
from batch_video import Store

class VideoPreview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.pp=self.base/'project.json'
        self.p={'project_id':'original-video-tests','version':'v2.3.1','groups':[{'id':'G02','episode':2},{'id':'G01','episode':1}]}
        save(self.pp,self.p);self.store=Store(self.base/'production.sqlite')
        self.file=self.base/'media'/'测试 视频#.mp4';self.file.parent.mkdir()
        self.file.write_bytes(b'\x00\x00\x00\x18ftypisom'+b'0'*40) # Original synthetic transport bytes, not a playable movie.
        self.task={'phase':'downloaded','attempts':1,'output':str(self.file),'sha256':sha(self.file),'bytes':self.file.stat().st_size}
    def tearDown(self):self.tmp.cleanup()
    def record(self,gid='G01',task=None):self.store.put(gid,gid,task or self.task)
    def test_three_entrances_at_initialization_without_network_or_video(self):
        from init_project import initialize
        source=self.base/'source.txt';source.write_text('第1集\n原创场景。',encoding='utf-8')
        with patch('urllib.request.urlopen',side_effect=AssertionError('No remote request')):r=initialize(self.base/'new',source)
        base=self.base/'new'
        self.assertTrue((base/'03_资产图/00_资产参考图总览.html').is_file());self.assertTrue((base/'07_跑前预览/全剧跑前预览.html').is_file());self.assertTrue((base/'08_视频'/PANEL).is_file())
        self.assertEqual(r['videos']['downloaded'],0);self.assertFalse((base/'production.sqlite').exists());self.assertEqual(r['media_calls'],0)
    def test_sorted_file_urls_and_no_remote_signed_result_exposed(self):
        self.task['result_url']='https://secret.invalid/private?signature=confidential';self.record();build(self.pp)
        data=load(self.base/'08_视频'/MANIFEST);self.assertEqual([r['id'] for r in data['items']],['G01','G02']);self.assertEqual(data['downloaded'],1)
        row=data['items'][0];self.assertIn('%23',row['src']);self.assertIn('%20',row['src']);self.assertFalse(row['content_review_performed']);self.assertFalse(data['generation_authorized']);self.assertNotIn('signature',callback(data));self.assertNotIn(str(self.file),callback(data))
    def test_no_changes_do_not_rewrite_shell_data_or_reset_timestamps(self):
        self.record();build(self.pp);out=self.base/'08_视频';before={n:(out/n).stat().st_mtime_ns for n in (PANEL,DATA,MANIFEST)}
        result=build(self.pp);self.assertFalse(result['data_changed']);self.assertFalse(result['panel_changed']);self.assertEqual(before,{n:(out/n).stat().st_mtime_ns for n in before})
    def test_new_result_changes_only_data_and_keeps_html_and_js_stable(self):
        build(self.pp);out=self.base/'08_视频';before={n:((out/n).read_bytes(),(out/n).stat().st_mtime_ns) for n in (PANEL,'video-preview.js')}
        self.record();r=build(self.pp);self.assertTrue(r['data_changed']);self.assertFalse(r['panel_changed']);self.assertEqual(r['downloaded'],1)
        self.assertEqual(before,{n:((out/n).read_bytes(),(out/n).stat().st_mtime_ns) for n in before})
    def test_cloud_success_or_downloading_not_counted_as_playable(self):
        self.record(task={'phase':'download','result_url':'https://invalid.test/video.mp4','attempts':1});r=build(self.pp);self.assertEqual(r['downloaded'],0)
        self.assertNotIn('files',json.loads(callback(load(self.base/'08_视频'/MANIFEST)).split('receive(',1)[1].rsplit(');',1)[0]))
    def test_missing_changed_or_unverified_file_is_visible_unavailable_not_regenerated(self):
        for change in ({'output':str(self.base/'missing.mp4')},{'bytes':1},{'sha256':''}):
            self.record(task={**self.task,**change});r=build(self.pp);self.assertEqual(r['downloaded'],0);row=load(self.base/'08_视频'/MANIFEST)['items'][0];self.assertEqual(row['phase'],'file_unavailable');self.assertFalse(row['file_available'])
    def test_shared_image_jobs_are_not_counted_as_video_groups(self):
        self.record();self.store.put('image:A01','image1',{'phase':'saved','output':'image.png','operation':'image_generate'})
        self.store.put('image:A02','image2',{'phase':'downloaded','output':'image.png','operation':'image_edit'})
        result=build(self.pp);self.assertEqual(result['total'],2);self.assertEqual(result['downloaded'],1)
        self.assertNotIn('image:A01',str(load(self.base/'08_视频'/MANIFEST)['items']))
    def test_cross_batch_tasks_preserved_from_read_only_store(self):
        self.record();self.record('G02',{'phase':'running','attempts':2});self.assertEqual(len(read_tasks(self.base)),2);r=build(self.pp);self.assertEqual(r['total'],2);self.assertEqual(r['downloaded'],1)
    def test_callback_escapes_markup_filenames(self):
        self.record();build(self.pp);data=load(self.base/'08_视频'/MANIFEST);data['items'][0]['filename']='</script><img src=x>';text=callback(data);self.assertNotIn('</script>',text);self.assertIn('\\u003c',text)
    @contextlib.contextmanager
    def server(self):
        self.record();s=create_server(self.pp);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        try:yield 'http://127.0.0.1:'+str(s.server_port)+'/'
        finally:s.shutdown();s.server_close();t.join()
    def test_http_get_head_range_suffix_seek_and_etag(self):
        with self.server() as url:
            data=load(self.base/'08_视频'/MANIFEST);mid=data['items'][0]['media_id'];target=url+'media/'+mid;raw=self.file.read_bytes()
            with urllib.request.urlopen(target) as r:self.assertEqual(r.read(),raw);etag=r.headers['ETag']
            with urllib.request.urlopen(urllib.request.Request(target,headers={'Range':'bytes=8-15'})) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),raw[8:16]);self.assertEqual(r.headers['Content-Range'],f'bytes 8-15/{len(raw)}')
            with urllib.request.urlopen(urllib.request.Request(target,headers={'Range':'bytes=-4'},method='HEAD')) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),b'');self.assertEqual(r.headers['Content-Length'],'4')
            with urllib.request.urlopen(urllib.request.Request(target,headers={'Range':'bytes=8-15','If-Range':'"other"'})) as r:self.assertEqual(r.status,200);self.assertEqual(r.read(),raw)
            with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(urllib.request.Request(target,headers={'Range':'bytes=999999-'}))
            self.assertEqual(c.exception.code,416)
    def test_http_only_exposes_registered_media_not_project_or_paths(self):
        (self.base/'credentials.txt').write_text('private-test',encoding='utf-8')
        with self.server() as url:
            for route in ('project.json','../credentials.txt','media/not-a-key','production.sqlite'):
                with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(url+route)
                self.assertEqual(c.exception.code,404)
            with urllib.request.urlopen(url+urllib.parse.quote(DATA)) as r:self.assertIn(b'/media/',r.read())
            with urllib.request.urlopen(url) as r:self.assertNotIn(b'href="../03_',r.read())
    def test_isolated_view_reads_existing_project_without_writing_production_and_updates(self):
        viewer=self.base/'isolated';self.record();before=self.pp.read_bytes()
        server=create_server(self.pp,view_root=viewer);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.assertFalse((self.base/'08_视频').exists());self.assertEqual(self.pp.read_bytes(),before)
            endpoint='http://127.0.0.1:'+str(server.server_port)+'/'+urllib.parse.quote(DATA)
            self.record('G02',{'phase':'running','attempts':2})
            with urllib.request.urlopen(endpoint) as response:payload=response.read().decode('utf-8')
            self.assertIn('running',payload);self.assertTrue((viewer/'08_视频'/PANEL).exists());self.assertFalse((self.base/'99_工作区').exists())
        finally:server.shutdown();server.server_close();thread.join()
    def test_byte_ranges_reject_multiple_empty_or_outside(self):
        self.assertEqual(byte_range('bytes=3-',10),(3,9,True));self.assertEqual(byte_range('bytes=-100',10),(0,9,True))
        for s in ('bytes=','bytes=-','bytes=9-2','bytes=12-15','bytes=1-3,6-8','bytes=-0'):
            with self.assertRaises(ValueError):byte_range(s,10)
    def test_dom_playback_survives_new_progress_and_explicit_version_switch(self):
        node=shutil.which('node')
        if not node:self.skipTest('Node.js unavailable; actual JS behavior not tested')
        result=subprocess.run([node,str(Path(__file__).with_name('test_video_client.cjs'))],capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('PASS:',result.stdout)

if __name__=='__main__':unittest.main()
