"""Stage changes refresh the protected entrances after durable state is saved."""
import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_stage_queue as stage_cases
from core import load,save,sha
from stage_queue import QUEUE,next_batch,start_batch,complete_batch,complete_image,block_image,retry_batch

class StageRefresh(unittest.TestCase):
    def setUp(self):
        self.case=stage_cases.StageQueue('test_first_five_complete_automatically_exposes_next_five_not_downstream');self.case.setUp();self.addCleanup(self.case.tearDown)
        self.events=[]
        from init_project import PANELS
        base=self.case.base;owned={}
        for folder,name,_ in PANELS.values():
            path=base/folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic owned panel',encoding='utf-8');owned[folder+'/'+name]=sha(path)
        rel='99_工作区/initialization-test.json';self.state=base/rel
        save(self.state,{'id':'TEST-INIT','status':'complete','owned_files':owned,'pending_files':{}})
        self.case.p['muflow_init_v240']={'id':'TEST-INIT','state_path':rel};self.case.sync()
    def fake_refresh(self,pp):
        q=load(Path(pp).parent/QUEUE);b=next((b for s in q['stages'].values() for b in s['batches'] if b['status'] in ('running','completed')),None)
        self.events.append({'batch':b['id'] if b else None,'status':b['status'] if b else None})
        return {'assets':{},'preview':{},'videos':{},'issues':[],'media_calls':0}
    def event_records(self):
        f=self.case.base/'99_工作区'/'阶段队列_面板刷新记录.jsonl'
        return [json.loads(line) for line in f.read_text(encoding='utf-8').splitlines()]
    def test_incomplete_initialization_never_refreshes(self):
        state=load(self.state);state['status']='render_partial';save(self.state,state)
        f=self.case.base/'07_跑前预览'/'全剧跑前预览.html';original=f.read_bytes()
        with patch('refresh_progress.refresh_owned',return_value={'assets':{},'preview':{},'videos':{},'issues':[]},create=True) as refresh:next_batch(self.case.pp)
        refresh.assert_not_called();self.assertEqual(f.read_bytes(),original);self.assertEqual(self.event_records()[-1]['status'],'待复核')
    def test_modified_user_panel_never_refreshes(self):
        f=self.case.base/'07_跑前预览'/'全剧跑前预览.html';f.write_text('用户自行修改的面板',encoding='utf-8');original=f.read_bytes()
        with patch('refresh_progress.refresh_owned',return_value={},create=True) as refresh:next_batch(self.case.pp)
        refresh.assert_not_called();self.assertEqual(f.read_bytes(),original);self.assertEqual(self.event_records()[-1]['status'],'待复核')
    def test_legacy_unowned_project_never_refreshes(self):
        del self.case.p['muflow_init_v240'];self.case.sync()
        with patch('refresh_progress.refresh_owned',return_value={},create=True) as refresh:next_batch(self.case.pp)
        refresh.assert_not_called();self.assertEqual(self.event_records()[-1]['status'],'待复核')
    def test_batch_start_and_completion_refresh_after_commit(self):
        c=self.case
        with patch('refresh_progress.refresh_owned',side_effect=self.fake_refresh,create=True):
            ready=next_batch(c.pp);start_batch(c.pp,ready['batch_id']);row=c.artifact({'id':ready['batch_id'],'episodes':ready['episodes']},'screenplay');complete_batch(c.pp,ready['batch_id'],[row],'本地合成测试')
        self.assertTrue(any(e['status']=='running' for e in self.events));self.assertTrue(any(e['status']=='completed' for e in self.events))
        records=self.event_records();self.assertIn('batch_started',[r['change'] for r in records]);self.assertIn('batch_completed',[r['change'] for r in records]);self.assertTrue(all(r['status']=='通过' for r in records))
    def test_failed_refresh_does_not_revoke_completed_batch(self):
        c=self.case
        with patch('refresh_progress.refresh_owned',side_effect=OSError('synthetic preview failure'),create=True):
            ready=next_batch(c.pp);start_batch(c.pp,ready['batch_id']);row=c.artifact({'id':ready['batch_id'],'episodes':ready['episodes']},'screenplay');done=complete_batch(c.pp,ready['batch_id'],[row],'本地合成测试')
        self.assertEqual(done['status'],'completed');q=load(c.base/QUEUE);self.assertEqual(q['stages']['screenplay']['batches'][0]['status'],'completed')
        records=self.event_records();last=next(x for x in reversed(records) if x['change']=='batch_completed');self.assertEqual(last['status'],'待复核');self.assertEqual(last['issues'][0]['error_type'],'OSError')
    def test_partial_image_and_status_changes_trigger_refresh(self):
        c=self.case;c.add_image('a');c.add_image('b')
        with patch('refresh_progress.refresh_owned',side_effect=self.fake_refresh,create=True):
            ready=c.finish_text_stages();start_batch(c.pp,ready['batch_id']);row=c.artifact({'id':ready['batch_id']},'image',asset_id='a')
            asset=next(x for x in c.p['assets'] if x['id']=='a');asset.update(local_path=row['path'],sha256=row['sha256']);c.sync()
            complete_image(c.pp,ready['batch_id'],'a',row,'真实合成图登记');block_image(c.pp,ready['batch_id'],'b','模拟图片待核对');retry_batch(c.pp,ready['batch_id'],'已核对准备恢复')
        changes=[x['change'] for x in self.event_records()];self.assertIn('image_completed',changes);self.assertIn('image_blocked',changes);self.assertIn('batch_retry',changes)

if __name__=='__main__':unittest.main()
