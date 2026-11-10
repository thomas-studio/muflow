"""Explicit production scope contracts; synthetic local data only."""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from workflow_gate import scope_groups,image_manifest
from asset_gallery import required_ids
from core import sha

class ExplicitScope(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        f=self.base/'one.png';f.write_bytes(b'synthetic reuse bytes')
        self.p={'project_id':'scope','episodes':[{'id':1},{'id':2}],
          'groups':[{'id':'G1','episode':1,'bindings':[{'asset_id':'A1'}]},
                    {'id':'G2','episode':2,'bindings':[{'asset_id':'A2'}]}],
          'assets':[{'id':'A1','kind':'scene','mode':'reuse','local_path':str(f),'sha256':sha(f)},
                    {'id':'A2','kind':'scene','mode':'reuse','local_path':str(self.base/'absent.png'),'sha256':'missing'}],
          'workflow':{},'settings':{'asset_review_mode':'agent'}}
    def tearDown(self):self.tmp.cleanup()
    def test_default_scope_includes_entire_show_and_missing_asset(self):
        self.assertEqual([g['id'] for g in scope_groups(self.p)],['G1','G2'])
        self.assertEqual(required_ids(self.p),{'A1','A2'})
        with self.assertRaises((FileNotFoundError,OSError)):image_manifest(self.p,self.base)
    def test_explicit_episode_scope_selects_only_needed_assets(self):
        self.p['workflow']['production_scope']={'episodes':[1],'user_evidence':'synthetic explicit request: episode 1 only'}
        self.assertEqual([g['id'] for g in scope_groups(self.p)],['G1'])
        self.assertEqual(required_ids(self.p),{'A1'})
        self.assertEqual([x['asset_id'] for x in image_manifest(self.p,self.base)],['A1'])
    def test_partial_scope_without_user_evidence_rejected(self):
        self.p['workflow']['production_scope']={'episodes':[1]}
        with self.assertRaisesRegex(ValueError,'Only the user'):scope_groups(self.p)
    def test_one_episode_cannot_exclude_one_of_its_groups(self):
        self.p['groups'].append({'id':'G1b','episode':1,'bindings':[{'asset_id':'A2'}]})
        self.p['workflow']['production_scope']={'episodes':[1],'user_evidence':'synthetic explicit episode 1'}
        self.assertEqual([g['id'] for g in scope_groups(self.p)],['G1','G1b'])
        with self.assertRaises((FileNotFoundError,OSError)):image_manifest(self.p,self.base)

if __name__=='__main__':unittest.main()
