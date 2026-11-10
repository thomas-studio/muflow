"""Scene provenance contract tests. No media service is called."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import sha
from scene_binding import audit

class SceneBinding(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        (self.base/'source.json').write_text(json.dumps({'paragraphs':[{'id':'SRC1','text':'34-1 沈家院子 外 日'},{'id':'SRC2','text':'34-2 沈家堂屋 内 日'}]},ensure_ascii=False),encoding='utf8')
        rows=[{'scene_id':'S1','episode':34,'scene_name':'沈家院子','source_id':'SRC1','location_id':'shen','space_id':'courtyard','inout':'外','time':'日','allowed_scene_asset_ids':['yard_day'],'evidence':'Synthetic source anchor SRC1'},
              {'scene_id':'S2','episode':34,'scene_name':'沈家堂屋','source_id':'SRC2','location_id':'shen','space_id':'hall','inout':'内','time':'日','allowed_scene_asset_ids':['hall_day'],'evidence':'Synthetic source anchor SRC2'}]
        self.p={'source_path':'source.json','scene_binding_baseline':{'source_sha256':sha(self.base/'source.json'),'evidence':'Synthetic source parse','scenes':rows},
          'scenes':[{'id':'S1','episode':34,'name':'沈家院子','source_id':'SRC1','inout':'外','time':'日'}, {'id':'S2','episode':34,'name':'沈家堂屋','source_id':'SRC2','inout':'内','time':'日'}],
          'assets':[{'id':'yard_day','kind':'scene','scene_identity':{'location_id':'shen','space_id':'courtyard','inout':'外','time':'日','evidence':'Synthetic inspection'}}, {'id':'hall_day','kind':'scene','scene_identity':{'location_id':'shen','space_id':'hall','inout':'内','time':'日','evidence':'Synthetic inspection'}}, {'id':'role','kind':'role'}]}
        self.g={'id':'G3401','bindings':[{'asset_id':'yard_day'},{'asset_id':'hall_day'},{'asset_id':'role'}],
          'shots':[{'id':'shot1','scene_id':'S1','primary_scene_asset_id':'yard_day','asset_ids':['yard_day','role']},
                   {'id':'shot2','scene_id':'S2','primary_scene_asset_id':'hall_day','asset_ids':['hall_day','role']}]}
    def tearDown(self):self.tmp.cleanup()
    def codes(self):return {x[0] for x in audit(self.p,self.base,[self.g])}
    def test_correct_mixed_scene_group(self):self.assertEqual(self.codes(),set())
    def test_original_34_wrong_hall_reference(self):
        shot=self.g['shots'][0];shot.update(primary_scene_asset_id='hall_day',asset_ids=['hall_day','role'])
        self.assertIn('SCENE_PRIMARY',self.codes())
    def test_wrong_identity_even_when_allowed_is_forged(self):
        self.p['scene_binding_baseline']['scenes'][0]['allowed_scene_asset_ids']=['hall_day'];self.g['shots'][0].update(primary_scene_asset_id='hall_day',asset_ids=['hall_day'])
        self.assertIn('SCENE_IDENTITY',self.codes())
    def test_wrong_time(self):
        self.p['assets'][0]['scene_identity']['time']='夜';self.assertIn('SCENE_IDENTITY',self.codes())
    def test_wrong_property_owner(self):
        self.p['assets'][0]['scene_identity']['location_id']='su';self.assertIn('SCENE_IDENTITY',self.codes())
    def test_missing_primary_and_missing_binding(self):
        del self.g['shots'][0]['primary_scene_asset_id'];self.assertIn('SCENE_PRIMARY',self.codes())
        self.g['shots'][0]['primary_scene_asset_id']='yard_day';self.g['bindings'].pop(0);self.assertIn('SCENE_BINDING',self.codes())
    def test_main_must_be_in_shot_list(self):
        self.g['shots'][0]['asset_ids']=['role'];self.assertIn('SCENE_BINDING',self.codes())
    def test_explicit_other_scene_background(self):
        s=self.g['shots'][0];s['asset_ids'].append('hall_day');self.assertIn('SCENE_SECONDARY',self.codes())
        s['secondary_scene_asset_ids']=['hall_day'];s['secondary_scene_evidence']='院内望向相邻堂屋门口';self.assertEqual(self.codes(),set())
    def test_legacy_project_fails_closed(self):
        del self.p['scene_binding_baseline'];self.assertIn('SCENE_BASELINE',self.codes())
    def test_source_changed_fails_closed(self):
        (self.base/'source.json').write_text('{}',encoding='utf8');self.assertIn('SCENE_BASELINE',self.codes())
    def test_scene_changed_requires_rebaseline(self):
        self.p['scenes'][0]['inout']='内';self.assertIn('SCENE_BASELINE',self.codes())
    def test_wrong_scene_type_and_no_evidence(self):
        self.p['assets'][0]['kind']='prop';self.assertIn('SCENE_PRIMARY',self.codes())
        self.p['assets'][0]['kind']='scene';self.p['assets'][0]['scene_identity']['evidence']='';self.assertIn('SCENE_IDENTITY',self.codes())
    def test_only_selected_groups(self):
        wrong=copy.deepcopy(self.g);wrong['id']='G3402';wrong['shots'][0]['primary_scene_asset_id']='missing'
        self.assertEqual(audit(self.p,self.base,[self.g]),[]);self.assertTrue(audit(self.p,self.base,[wrong]))

    def test_review_subject_changes_when_baseline_or_image_changes(self):
        from review_state import group_subject,asset_subject
        initial=group_subject(self.p,self.g)
        self.p['scene_binding_baseline']['scenes'][0]['space_id']='changed'
        self.assertNotEqual(group_subject(self.p,self.g),initial)
        self.p['scene_binding_baseline']['scenes'][0]['space_id']='courtyard'
        initial=group_subject(self.p,self.g);self.p['assets'][0]['sha256']='new-image'
        self.assertNotEqual(group_subject(self.p,self.g),initial)
        old=asset_subject(self.p,self.p['assets'][0]);self.p['assets'][0]['scene_identity']['time']='夜'
        self.assertNotEqual(asset_subject(self.p,self.p['assets'][0]),old)

    def test_validate_ready_blocks_wrong_scene_but_local_marks_pending(self):
        from unittest.mock import patch
        from validate_project import validate
        p=copy.deepcopy(self.p);p.update(schema_version=2,version='synthetic',episodes=[{'id':34}],dialogues=[],groups=[copy.deepcopy(self.g)],settings={'group_target_seconds':30,'onscreen_text':False,'subtitles':False,'background_music':False,'ambient_bed':False})
        g=p['groups'][0];g.update(episode=34,expected_seconds=30)
        for sh in g['shots']:sh.update(seconds=15,entry_state='synthetic',exit_state='synthetic',camera='中景',action='站立',speech=[])
        g['shots'][0].update(primary_scene_asset_id='hall_day',asset_ids=['hall_day'])
        for sc in p['scenes']:sc['transition']='明确转场'
        with patch('audit_project_network.audit',return_value={'issues':[]}),patch('source_coverage.coverage',return_value={'complete':True}),patch('timing_plan.build_plan',return_value={'issues':[]}),patch('continuity.audit',return_value=[]):
            ready=validate(p,self.base,stage='ready');local=validate(p,self.base,stage='local')
        self.assertIn('SCENE_PRIMARY',{i['id'] for i in ready['issues']})
        self.assertNotIn('SCENE_PRIMARY',{i['id'] for i in local['issues']})
        pending=next(c for c in local['checks'] if c['id']=='SCENE_BINDING_PENDING')
        self.assertEqual(pending['status'],'待复核');self.assertIn('SCENE_PRIMARY',{i['id'] for i in pending['details']})

    def test_actual_html_reports_wrong_scene_as_pending(self):
        from run_offline_tests import Mock,fixture
        from test_v2 import upgrade
        from core import save,load
        from build_preview import build
        from unittest.mock import patch
        mock=Mock()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);p,cfg=fixture(base,mock.url);upgrade(p,cfg,base)
                shot=p['groups'][0]['shots'][0];shot['primary_scene_asset_id']='A1';save(base/'project.json',p)
                with patch('urllib.request.urlopen',side_effect=AssertionError('preview must not use network')):
                    result=build(base/'project.json',base/'preview')
                doc=Path(result['panel']).read_text(encoding='utf8');checks=load(base/'preview/preview_checks.json')
                self.assertEqual(result['media_calls'],0);self.assertFalse(result['prepared'])
                self.assertIn('场景绑定待复核',doc);self.assertIn(shot['id'],doc);self.assertIn('SCENE_PRIMARY',doc)
                row=next(x for x in checks['scene_binding_pending'] if x['object']==shot['id'])
                self.assertEqual(row['status'],'待复核');self.assertEqual(row['id'],'SCENE_PRIMARY')
        finally:mock.close()

if __name__=='__main__':unittest.main()
