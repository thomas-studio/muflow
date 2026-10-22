"""Original local fixtures for readable previews and independent source coverage.

No credentials, network services, production novels or private reference paths.
"""
import copy
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from PIL import Image
from core import load, save, sha
from build_preview import build, compile_jobs
from build_text_preview import build as text_preview
from source_coverage import coverage, episode_number
from preview_ui import PANEL


class Page(HTMLParser):
    def __init__(self,text):
        super().__init__();self.tags=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))


def original_project(base,total=5,prepared=5,images=True):
    base=Path(base);(base/'prompts').mkdir(exist_ok=True);(base/'images').mkdir(exist_ok=True)
    save(base/'source.json',{'paragraphs':[{'id':f'P{ep}','text':f'第{ep}集 原创短例\n小雨站在门边，收好钥匙。'} for ep in range(1,total+1)]})
    assets=[]
    for index,(aid,name,kind) in enumerate((('A1','小雨','role'),('S1','屋门口','scene'))):
        row={'id':aid,'name':name,'kind':kind,'mode':'generate','prompt':'原创本地占位图；只供测试。'}
        if images:
            file=base/'images'/(aid+'.png');Image.new('RGB',(160,90),(80+index*60,110,140)).save(file)
            row.update(local_path=str(file),sha256=sha(file))
        assets.append(row)
    project={'schema_version':1,'project_id':'original-preview-fixture','version':'draft-1','source_path':'source.json',
             'settings':{'video_ratio':'9:16','asset_review_mode':'joint'},'assets':assets,
             'episodes':[{'id':ep,'title':'门边的钥匙','ending':'小雨收好钥匙。'} for ep in range(1,prepared+1)],
             'scenes':[],'dialogues':[],'groups':[]}
    for ep in range(1,prepared+1):
        gid=f'E{ep:02d}G01';sid=f'{ep}-1';did=f'D{ep}'
        file=base/'prompts'/(gid+'.txt');file.write_text(f'第{ep}集 · 原创请求\n@小雨=图1 / @屋门口=图2\n执行已登记动作与台词。',encoding='utf-8')
        speech={'id':did,'speaker':'小雨','channel':'内心','emotion':'轻松确认','text':'钥匙已经收好了。'}
        project['scenes'].append({'id':sid,'episode':ep,'name':'屋门口','time':'白天','transition':'同日承前'})
        project['dialogues'].append({**speech,'source_ids':[f'P{ep}'],'evidence':'原创测试台词'})
        project['groups'].append({'id':gid,'episode':ep,'expected_seconds':30,'prompt_path':str(file),
                'bindings':[{'name':'小雨','asset_id':'A1'},{'name':'屋门口','asset_id':'S1'}],
                'review':{'status':'待审','evidence':'原创测试，尚非用户确认'},
                'shots':[{'id':gid+'S01','scene_id':sid,'seconds':30,'camera':'中近景／门内朝外／定机',
                          'action':'小雨右手收起钥匙，左手扶门。','entry_state':'左手扶门，右手握钥匙。',
                          'exit_state':'右手已收好钥匙，左手仍扶门。','speech':[speech],
                          'sound_effects':['钥匙进入口袋时一声轻响'],'asset_ids':['A1','S1']}]})
    save(base/'project.json',project)
    return project


class PreviewViews(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
    def tearDown(self):
        if self.base.resolve().parent!=Path(tempfile.gettempdir()).resolve():
            raise RuntimeError('Unexpected cleanup target')
        self.tmp.cleanup()
    def test_one_dom_two_views_real_files_and_unique_resources(self):
        original_project(self.base)
        with patch('urllib.request.urlopen',side_effect=AssertionError('preview attempted network')):
            result=build(self.base/'project.json',self.base/'preview')
        file=self.base/'preview'/PANEL;doc=file.read_text(encoding='utf-8');page=Page(doc)
        self.assertEqual(result['media_calls'],0)
        self.assertIn('本地审阅',doc);self.assertIn('OSS执行核对',doc)
        self.assertIn('position:sticky',doc);self.assertIn('当前版本',doc);self.assertIn('更新时间',doc)
        self.assertEqual(len([a for t,a in page.tags if t=='table' and a.get('class')=='shots']),5)
        self.assertNotIn('data:image/',doc);self.assertNotIn('<script',doc)
        images=[a['src'] for t,a in page.tags if t=='img']
        self.assertEqual(len(images),10);self.assertEqual(len(set(images)),2)
        for link in images:
            self.assertTrue((file.parent/unquote(link)).is_file())
        originals=[a['href'] for t,a in page.tags if t=='a' and a.get('title')=='查看原图']
        self.assertEqual(len(set(originals)),2)
        for link in originals:
            self.assertTrue((file.parent/unquote(link)).is_file())
        self.assertEqual(len(list((file.parent/'preview_resources').iterdir())),4)
        for text in ('镜号','时长','景别／机位／运动','画面与动作','对白／音效','承上','末帧','下一组','场景／日夜','内心','轻松确认'):
            self.assertIn(text,doc)
        self.assertEqual(doc.count('<div class="bind">'),5)
        self.assertIn('#view-execution:checked~.wrap',doc)
        self.assertIn('尚未就绪',doc)
    def test_refresh_fixed_entry_preserves_previous_sidecars_and_originals(self):
        p=original_project(self.base,2,2);before=sha(self.base/'images/A1.png')
        first=build(self.base/'project.json',self.base/'preview');old=Path(first['panel']).read_text(encoding='utf-8')
        p['groups'][0]['shots'][0]['action']='小雨收好钥匙，稍稍点头。';p['version']='draft-2';save(self.base/'project.json',p)
        second=build(self.base/'project.json',self.base/'preview')
        archive=Path(second['archive']);self.assertTrue((archive/PANEL).is_file())
        archived=(archive/PANEL).read_text(encoding='utf-8')
        self.assertIn('src="../../preview_resources/',archived)
        self.assertIn('href="#ep-1"',archived)
        self.assertTrue((archive/'compiled_jobs.json').is_file())
        self.assertNotEqual(old,Path(second['panel']).read_text(encoding='utf-8'))
        self.assertEqual(before,sha(self.base/'images/A1.png'))
        self.assertEqual(first['panel'],second['panel'])
    def test_draft_without_images_profile_or_upload_still_outputs_truthful_panel(self):
        original_project(self.base,3,1,images=False)
        result=build(self.base/'project.json',self.base/'preview')
        doc=Path(result['panel']).read_text(encoding='utf-8');checks=load(self.base/'preview/preview_checks.json')
        self.assertIn('图片待生成或待登记',doc);self.assertIn('未上传',doc);self.assertIn('待传入本机配置',doc)
        self.assertTrue(checks['local_preview_created']);self.assertFalse(checks['execution_ready'])
        self.assertEqual(checks['progress']['local_images'],0)
        self.assertEqual(len(load(self.base/'preview/compiled_jobs.json')['unprepared']),1)
        self.assertIn('id="ep-3"',doc);self.assertIn('本集尚未准备生成组',doc)
        for name in ('compiled_jobs','quote_snapshot','run_scope','preview_checks','timing_plan'):
            self.assertTrue((self.base/'preview'/(name+'.json')).is_file())
    def test_text_command_uses_same_entry_while_incomplete(self):
        original_project(self.base,2,1,images=False)
        result=text_preview(self.base/'project.json',self.base/'preview')
        self.assertFalse(result['complete']);self.assertIsNone(result['manifest_digest'])
        link=(self.base/'preview/全剧文字与提示词审阅.html').read_text(encoding='utf-8')
        self.assertIn(PANEL+'#text-deliverables',__import__('urllib.parse',fromlist=['unquote']).unquote(link))
        self.assertEqual(Path(result['panel']).resolve(),(self.base/'07_跑前预览'/PANEL).resolve())
        self.assertFalse((self.base/'preview'/PANEL).exists());self.assertNotIn('<table',link)
        doc=Path(result['panel']).read_text(encoding='utf-8')
        self.assertIn('文字审阅快照',doc);self.assertIn('资产提示词',doc)
    def test_declared_profile_values_only_and_review_metadata_not_compiled(self):
        p=original_project(self.base,1,1);before=compile_jobs(p,self.base)['items']
        p['groups'][0]['review']={'status':'通过','evidence':'增加实际检查记录','recorded_at':'2100-01-01'}
        self.assertEqual(before,compile_jobs(p,self.base)['items'])
        save(self.base/'profile.json',{'video':{'model':'synthetic-user-chosen','resolution':'example','max_price_per_attempt_yuan':1.25},'secret':'do-not-copy'})
        result=build(self.base/'project.json',self.base/'preview',self.base/'profile.json')
        doc=Path(result['panel']).read_text(encoding='utf-8');quote=load(self.base/'preview/quote_snapshot.json')
        self.assertEqual(quote['parameters']['model'],'synthetic-user-chosen')
        self.assertIn('1.25',doc);self.assertNotIn('do-not-copy',doc)
    def test_no_registered_episodes_can_initialize_panel(self):
        p=original_project(self.base,4,0,images=False)
        result=build(self.base/'project.json',self.base/'preview')
        doc=Path(result['panel']).read_text(encoding='utf-8')
        self.assertIn('id="ep-4"',doc);self.assertFalse(result['prepared'])
    def test_cross_episode_shared_group_uses_actual_boundary_for_bridge(self):
        p=original_project(self.base,2,2);group=p['groups'][0];first=group['shots'][0]
        first['seconds']=15;first['exit_state']='第一段结束状态'
        second=copy.deepcopy(first);second['id']='SECOND';second['entry_state']='第二段开始状态';second['exit_state']='第二段结束状态'
        group['shots']=[first,second]
        group['uses']=[{'episode':1,'shot_ids':[first['id']]},{'episode':2,'shot_ids':['SECOND']}]
        p['groups']=p['groups'][:1];save(self.base/'project.json',p)
        result=build(self.base/'project.json',self.base/'preview');doc=Path(result['panel']).read_text(encoding='utf-8')
        first_ep=doc.split('<section class="ep" id="ep-1">',1)[1].split('<section class="ep" id="ep-2">',1)[0]
        second_ep=doc.split('<section class="ep" id="ep-2">',1)[1]
        self.assertIn('下一组／下一集：第二段开始状态',first_ep)
        self.assertIn('承上：第一段结束状态',second_ep)
    def test_asset_table_is_an_actual_visible_link_and_empty_baseline_is_not_stale(self):
        p=original_project(self.base,2,1)
        table=self.base/'assets.md';table.write_text('原创资产拆解表',encoding='utf-8')
        p['deliverables']={'asset_table':'assets.md'}
        p['episode_baseline']={'confirmed':False,'episode_ids':[],'source_sha256':None,'evidence':None}
        save(self.base/'project.json',p)
        self.assertEqual(coverage(p,self.base)['basis_status'],'verified')
        result=build(self.base/'project.json',self.base/'preview');doc=Path(result['panel']).read_text(encoding='utf-8')
        self.assertIn('href="'+table.as_uri()+'">资产拆解表</a>',doc)
        self.assertIn('id="readiness"',doc);self.assertIn('准备状态',doc)
    def test_project_initialization_creates_two_truthful_local_entrances_zero_http(self):
        from init_project import initialize
        from asset_gallery import GALLERY
        source=self.base/'original.txt';source.write_text('第一章 原创小说\n小雨把钥匙收好。',encoding='utf-8')
        target=self.base/'new-project'
        with patch('urllib.request.urlopen',side_effect=AssertionError('initialization attempted network')):
            result=initialize(target,source,'original-new-fixture')
        gallery=target/'03_资产图'/GALLERY;panel=target/'07_跑前预览'/PANEL
        self.assertTrue(gallery.is_file());self.assertTrue(panel.is_file())
        doc=panel.read_text(encoding='utf-8')
        self.assertIn('准备状态：准备中',doc);self.assertIn('资产拆解表：待编制',doc)
        self.assertIn('独立基准 待核实',doc)


class SourceCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
    def tearDown(self):
        if self.base.resolve().parent!=Path(tempfile.gettempdir()).resolve():
            raise RuntimeError('Unexpected cleanup target')
        self.tmp.cleanup()
    def test_full_source26_registered3_is_not_complete(self):
        p=original_project(self.base,26,3);result=coverage(p,self.base)
        self.assertEqual(result['source_episodes'],list(range(1,27)))
        self.assertEqual(result['missing'],list(range(4,27)))
        self.assertFalse(result['complete']);self.assertFalse(result['full_complete'])
    def test_explicit_three_episode_scope_retains_full_source_denominator(self):
        p=original_project(self.base,26,3);p['workflow']={'production_scope':{'episodes':[1,2,3],'user_evidence':'合成测试事件：只制作前三集'}}
        result=coverage(p,self.base)
        self.assertTrue(result['scope_explicit']);self.assertTrue(result['complete'])
        self.assertFalse(result['full_complete']);self.assertEqual(result['source_count'],26)
        self.assertEqual(result['requested_episodes'],[1,2,3])
    def test_new_source35_current5_displays_every_missing_episode(self):
        p=original_project(self.base,35,5);result=coverage(p,self.base)
        self.assertEqual(result['missing'],list(range(6,36)))
        built=build(self.base/'project.json',self.base/'preview')
        self.assertIn('id="ep-35"',Path(built['panel']).read_text(encoding='utf-8'))
    def test_chinese_headings_txt_and_json_are_episodes_but_chapters_are_not(self):
        (self.base/'source.txt').write_text('第一集开门\n第二集：收钥匙\n第十一集 回到屋里\n第二十三章 不是一集\n',encoding='utf-8')
        p={'source_path':'source.txt','episodes':[{'id':1},{'id':2},{'id':11}],'groups':[]}
        self.assertEqual(coverage(p,self.base)['source_episodes'],[1,2,11])
        self.assertEqual(episode_number('一百零二'),102)
        save(self.base/'source.json',{'paragraphs':[{'id':'A','text':'第三集 原创标题'},{'id':'B','text':'第三章 小说'}]})
        p['source_path']='source.json';self.assertEqual(coverage(p,self.base)['source_episodes'],[3])
    def test_novel_registration_is_not_independent_baseline_and_confirmed_hash_is_bound(self):
        p=original_project(self.base,5,5);save(self.base/'source.json',{'paragraphs':[{'id':'P1','text':'第一章 原创小说，只有章节。'}]})
        report=coverage(p,self.base);self.assertEqual(report['basis_status'],'unverified');self.assertFalse(report['complete'])
        p['episode_baseline']={'confirmed':True,'episode_ids':list(range(1,6)),'source_sha256':sha(self.base/'source.json'),'evidence':'原创小说分集基准已确认的合成测试'}
        self.assertTrue(coverage(p,self.base)['complete'])
        save(self.base/'source.json',{'paragraphs':[{'id':'P1','text':'第一章 原稿已经变更。'}]})
        self.assertEqual(coverage(p,self.base)['basis_status'],'unverified')
        self.assertFalse(coverage(p,self.base)['complete'])
    def test_registered_episode_without_actual_shots_is_a_gap(self):
        p=original_project(self.base,2,2);p['groups'][1]['shots']=[]
        report=coverage(p,self.base);self.assertEqual(report['missing_groups'],[2]);self.assertFalse(report['complete'])
    def test_use_of_nonexistent_shot_does_not_claim_episode_coverage(self):
        p=original_project(self.base,2,2)
        p['groups'][1]['uses']=[{'episode':2,'shot_ids':['NONEXISTENT']}]
        report=coverage(p,self.base)
        self.assertEqual(report['missing_groups'],[2]);self.assertFalse(report['complete'])


if __name__=='__main__':
    unittest.main()
