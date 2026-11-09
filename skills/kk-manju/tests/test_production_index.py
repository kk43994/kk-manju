import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from companion_store import Store
from libtv_sync import SyncService
import production_index as index
import production_catalog as catalog

class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'state')
        self.service=SyncService(self.store,self.root)
        with self.store.connect() as c:c.execute('INSERT INTO libtv_sync_settings VALUES(?,?)',('account','a'))
        movie=self.root/'original.mp4';movie.write_bytes(b'original')
        catalog.import_catalog(self.store,{'project':'p','title':'测试剧','episodes':[{'id':'EP001','name':'第一集','versions':[{'id':'V3','shots':[{'id':'V09','title':'抛瓶','preferredCandidate':'original','candidates':[{'id':'original','path':str(movie),'node':'v-old'}]}]}]}],'assets':[]})
        self.graph={'media':[
            {'id':'v1','type':'video','name':'EP002｜V1｜E2V01 开场','taskStatus':2,'duration':10,'resourceIds':['res1'],'urls':['https://libtv-res.liblib.art/one.mp4']},
            {'id':'v2','type':'video','name':'EP002｜V2｜E2V02 松腕','taskStatus':1,'urls':[]}],
            'storyboards':[{'name':'EP002｜V1｜分镜','rows':[{'id':'E2V01','durationSeconds':10},{'id':'E2V02','durationSeconds':14}]}]}
        self.snapshot()
        self.planroot=self.root/'production';self.planroot.mkdir()
        self.plan={'project_id':'p','revision':'EP002-V2-R1','jobs':[{'id':'E2V02','kind':'video','duration_seconds':14,'shots':[{'purpose':'松腕，近景'}]},{'id':'E2V03','kind':'video','duration_seconds':12,'shots':[]}]}
        (self.planroot/'plan.json').write_text(json.dumps(self.plan))
        index.register(self.store,'p',self.planroot,[{'episode':'EP002','version':'V2','fromVersion':'V1','shot':'E2V01','reason':'导演明确沿用开场'}])
    def tearDown(self):self.tmp.cleanup()
    def snapshot(self):
        with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO libtv_snapshots VALUES(?,?,?,?)',('a','p',json.dumps(self.graph),time.time()))
    def read(self):return catalog.read(self.store,{'project':'p','includePending':True})
    def ep2(self):return next(e for e in self.read()['episodes'] if e['id']=='EP002')
    def test_discovers_episode_versions_pending_and_explicit_reuse(self):
        ep=self.ep2();self.assertEqual([v['id'] for v in ep['versions']],['V2','V1'])
        shots=ep['versions'][0]['shots'];self.assertEqual([s['id'] for s in shots],['E2V01','E2V02','E2V03'])
        self.assertEqual(len(shots[0]['candidates']),1);self.assertEqual(shots[1]['candidates'],[])
        self.assertEqual(shots[1]['jobStatus'],'running')
        self.assertNotIn('remoteUrl',json.dumps(ep));self.assertNotIn('originAccount',json.dumps(ep))
        self.assertTrue(shots[0]['candidates'][0]['available'])
    def test_completed_output_appears_automatically_without_duplicates(self):
        self.ep2();self.graph['media'][1].update(taskStatus=2,urls=['https://libtv-res.liblib.art/two.mp4'],resourceIds=['res2'])
        self.snapshot();ep=self.ep2();self.ep2()
        shot=ep['versions'][0]['shots'][1];self.assertEqual(len(shot['candidates']),1)
        self.assertEqual(shot['jobStatus'],'succeeded');self.assertIsNone(shot['adopted'])
        self.assertEqual(self.service.source('p',shot['candidates'][0]['id'])[0],'https://libtv-res.liblib.art/two.mp4')
    def test_remote_version_changes_preserve_history_and_adoption(self):
        shot=self.ep2()['versions'][1]['shots'][0];old=shot['candidates'][0]['id']
        catalog.adopt(self.store,{'id':'choose','project':'p','episode':'EP002','version':'V1','shot':'E2V01','candidate':old,'expectedRevision':0})
        self.graph['media'][0].update(urls=['https://libtv-res.liblib.art/new.mp4'],resourceIds=['res-new']);self.snapshot()
        shot=self.ep2()['versions'][1]['shots'][0]
        self.assertEqual(len(shot['candidates']),2);self.assertEqual(shot['adopted'],old)
        self.assertEqual(self.service.source('p',old)[0],'https://libtv-res.liblib.art/one.mp4')
        first=self.read()['episodes'][0]['versions'][0]['shots'][0]
        self.assertEqual(first['preferredCandidate'],'original');self.assertEqual(len(first['candidates']),1)
    def test_cross_episode_names_and_references_cannot_create_wrong_shots(self):
        self.graph['media'].append({'id':'bad','type':'video','name':'EP002｜V2｜E3V02','urls':['https://libtv-res.liblib.art/bad.mp4'],'taskStatus':2})
        self.snapshot();self.assertEqual(len(self.ep2()['versions'][0]['shots']),3)
        with self.assertRaises(ValueError):self.service.source('other',self.ep2()['versions'][1]['shots'][0]['candidates'][0]['id'])
    def test_abbreviated_repair_label_attaches_to_existing_shot(self):
        self.graph['media'].append({'id':'repair','type':'video','name':'EP002｜V2-R2｜V02 松腕返修','urls':['https://libtv-res.liblib.art/repair.mp4'],'resourceIds':['repair'],'taskStatus':2})
        self.snapshot();shots=self.ep2()['versions'][0]['shots']
        self.assertEqual([s['id'] for s in shots],['E2V01','E2V02','E2V03'])
        self.assertEqual(len(shots[1]['candidates']),1)
    def test_local_plan_progress_is_read_live_without_writing_sources(self):
        state={'project_id':'p','revision':self.plan['revision'],'jobs':{'E2V03':{'status':'claimed'}}}
        path=self.planroot/'production-state.json';path.write_text(json.dumps(state));before=path.read_bytes()
        self.assertEqual(self.ep2()['versions'][0]['shots'][2]['jobStatus'],'claimed');self.assertEqual(path.read_bytes(),before)
        state['jobs']['E2V03']['status']='unknown';path.write_text(json.dumps(state))
        self.assertEqual(self.ep2()['versions'][0]['shots'][2]['jobStatus'],'unknown')
    def test_completed_task_resolves_same_submission_but_not_new_repair(self):
        state={'project_id':'p','revision':self.plan['revision'],'jobs':{'E2V02':{'status':'submitted','task_id':'task2'}}}
        path=self.planroot/'production-state.json';path.write_text(json.dumps(state))
        self.graph['media'][1].update(taskId='task2',taskStatus=2,urls=['https://libtv-res.liblib.art/two.mp4']);self.snapshot()
        self.assertEqual(self.ep2()['versions'][0]['shots'][1]['jobStatus'],'succeeded')
        state['jobs']['E2V02']['task_id']='new-repair';path.write_text(json.dumps(state))
        self.assertEqual(self.ep2()['versions'][0]['shots'][1]['jobStatus'],'submitted')
    def test_legacy_panels_receive_only_playable_rows(self):
        data=catalog.read(self.store,{'project':'p'})
        self.assertEqual(len(data['episodes']),2)
        self.assertTrue(all(s['candidates'] for e in data['episodes'] for v in e['versions'] for s in v['shots']))
    def test_auto_sync_throttles_and_never_launches_generation(self):
        with patch.object(self.service,'start') as start:
            self.service.ensure_auto('p');start.assert_called_once_with('p');start.reset_mock()
            with self.store.connect() as c:c.execute('INSERT INTO libtv_sync_runs VALUES(?,?,?,?,?,?,?)',('recent','p','completed','完成',None,time.time(),time.time()))
            self.service.ensure_auto('p');start.assert_not_called()

if __name__=='__main__':unittest.main()
