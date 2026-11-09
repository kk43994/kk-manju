import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from companion_store import Store
from codex_bridge import CodexBridge, READ_TOOLS
from libtv_sync import SyncService
import production_catalog as production


class FakeBridge:
    def __init__(self):
        self.identity={'userId':1,'accountId':2}
        self.status={'codex':'connected','libtv':'connected','checkedAt':time.time(),'error':None}
        self.calls=[];self.failure=None;self.switch=False;self.checks=0;self.asset_version='v1'

    def check(self):
        self.checks+=1
        if self.switch and self.checks%2==0:self.identity={'userId':3,'accountId':4}
        return self.status

    def call(self,tool,args):
        assert tool in READ_TOOLS
        self.calls.append((tool,args))
        if self.failure==tool:raise RuntimeError('断线 token=private-value')
        if tool=='list_projects':
            projects=[{'uuid':f'p{i}','name':f'项目{i}'} for i in range(205)]
            start=(args['page']-1)*args['pageSize']
            return {'data':{'projectMetaList':projects[start:start+args['pageSize']],'total':205}}
        if tool=='read_project':
            return {'projectId':args['projectId'],'revision':'r1','nodes':[
                {'id':'group','type':'group','data':{'name':'第一集'}},
                {'id':'v1','type':'video','parentId':'group','data':{'name':'V09','url':['https://libtv-res.liblib.art/example.mp4']}}]}
        if tool=='search_assets':return {'items':[{'assetId':'a1','name':'人物','folderId':1,'assetVersion':self.asset_version}],'total':1}
        if tool=='list_asset_folders':return {'folders':[{'id':1,'name':'角色'}],'total':1}
        if tool=='get_asset':return {'asset':{'assetId':'a1','items':[{'index':0,'type':'image','mediaUrls':['https://libtv-res.liblib.art/a.jpg']} ]}}
        raise AssertionError(tool)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'state');self.service=SyncService(self.store,self.root)
        self.bridge=FakeBridge();self.service.bridge=self.bridge
        movie=self.root/'movie.mp4';movie.write_bytes(b'fixture')
        production.import_catalog(self.store,{'project':'p0','episodes':[{'id':'EP001','versions':[{'id':'V3','shots':[{'id':'V09','candidates':[{'id':'r1','path':str(movie)}]}]}]}],'assets':[]})
        production.adopt(self.store,{'id':'choice1','project':'p0','episode':'EP001','version':'V3','shot':'V09','candidate':'r1','expectedRevision':0})
        self.store.issue({'id':'issue1','project':'p0','node':'v1','timecode':1,'category':'画面','description':'保留初版表演'})

    def tearDown(self):self.temp.cleanup()

    def run_sync(self,key='run',project='p0',full=False):
        with self.store.connect() as c:c.execute('INSERT INTO libtv_sync_runs VALUES(?,?,?,?,?,?,?)',(key,project,'running','开始',None,time.time(),None))
        self.service.lock.acquire();self.service._run(key,project,full)
        with self.store.connect() as c:return dict(c.execute('SELECT * FROM libtv_sync_runs WHERE id=?',(key,)).fetchone())

    def test_all_pages_and_real_details_preserve_local_choices(self):
        self.assertEqual(self.run_sync()['status'],'completed')
        result=self.service.read('p0')
        self.assertEqual(len(result['projects']),205)
        self.assertEqual([a['page'] for t,a in self.bridge.calls if t=='list_projects'],[1,2,3])
        self.assertEqual(result['media'][0]['group'],'第一集')
        self.assertTrue(result['assets'][0]['hasMedia'])
        self.assertNotIn('libtv-res',json.dumps(result))
        self.assertEqual(self.service.source('p0','asset:a1:0')[0],'https://libtv-res.liblib.art/a.jpg')
        self.assertEqual(production.read(self.store,{'project':'p0'})['episodes'][0]['versions'][0]['shots'][0]['adopted'],'r1')
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT description FROM issues').fetchone()[0],'保留初版表演')

    def test_partial_failure_preserves_complete_snapshot_and_redacts_error(self):
        self.run_sync();before=self.service.raw('p0')
        self.bridge.failure='get_asset';self.bridge.asset_version='changed'
        failed=self.run_sync('retry')
        self.assertEqual(failed['status'],'failed');self.assertNotIn('private-value',failed['error'])
        self.assertEqual(self.service.raw('p0'),before)
        self.assertFalse(self.service.lock.locked())

    def test_incremental_reuses_directory_and_unchanged_asset_details(self):
        self.run_sync();self.bridge.calls.clear()
        self.assertEqual(self.run_sync('incremental')['status'],'completed')
        self.assertEqual([t for t,a in self.bridge.calls],['read_project','search_assets'])
        stats=self.service.read('p0')['syncStats']
        self.assertEqual(stats['assetsReused'],1);self.assertEqual(stats['assetsRefreshed'],0)
        self.assertTrue(stats['projectsReused'])

    def test_changed_asset_refreshes_only_its_detail(self):
        self.run_sync();self.bridge.calls.clear();self.bridge.asset_version='v2'
        self.assertEqual(self.run_sync('changed')['status'],'completed')
        self.assertEqual([t for t,a in self.bridge.calls],['read_project','search_assets','get_asset'])
        self.assertEqual(self.service.read('p0')['syncStats']['assetsRefreshed'],1)

    def test_full_refresh_bypasses_cache_and_normal_sync_does_not_extend_directory_ttl(self):
        self.run_sync();checked=self.service.raw('p0')[0]['projectsCheckedAt']
        self.run_sync('normal');self.assertEqual(self.service.raw('p0')[0]['projectsCheckedAt'],checked)
        self.bridge.calls.clear();self.run_sync('full',full=True)
        self.assertEqual([t for t,a in self.bridge.calls].count('list_projects'),3)
        self.assertIn('get_asset',[t for t,a in self.bridge.calls])
        self.assertFalse(self.service.read('p0')['syncStats']['projectsReused'])

    def test_assets_without_media_still_appear_in_library(self):
        real_call=self.bridge.call
        with patch.object(self.bridge,'call',side_effect=lambda tool,args: {'asset':{'assetId':'a1','items':[]}} if tool=='get_asset' else real_call(tool,args)):
            self.assertEqual(self.run_sync()['status'],'completed')
        result=self.service.read('p0')
        self.assertEqual(result['assetCount'],1)
        self.assertEqual(len(result['assets']),1)
        self.assertFalse(result['assets'][0]['hasMedia'])

    def test_account_changes_cannot_commit_mixed_snapshot(self):
        self.run_sync()
        with self.store.connect() as c:before=c.execute('SELECT data FROM libtv_snapshots').fetchone()[0]
        self.bridge.switch=True
        self.assertEqual(self.run_sync('switch')['status'],'failed')
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT data FROM libtv_snapshots').fetchone()[0],before)
        self.assertEqual(self.service.read('p0')['media'],[])

    def test_other_project_has_no_current_project_media(self):
        self.run_sync()
        self.assertEqual(self.service.read('p1')['media'],[])
        with self.assertRaises(ValueError):self.service.source('p1','v1')
        self.assertEqual(self.run_sync('foreign','not-authorized')['status'],'failed')

    def test_duplicate_pages_and_missing_identity_rejected(self):
        with patch.object(self.service,'phase'),patch.object(self.bridge,'call',return_value={'items':[{'id':'same'}],'total':2}):
            with self.assertRaisesRegex(ValueError,'重复'):self.service.pages('search_assets',{},lambda d:(d['items'],d['total']),'unused','读取')
        with patch.object(self.service,'phase'),patch.object(self.bridge,'call',return_value={'items':[{}],'total':1}):
            with self.assertRaisesRegex(ValueError,'身份'):self.service.pages('search_assets',{},lambda d:(d['items'],d['total']),'unused','读取')

    def test_repeated_clicks_share_one_running_sync(self):
        entered=threading.Event();release=threading.Event();done=threading.Event()
        real_run=self.service._run
        def delayed(key,project,full=False):
            entered.set();release.wait(3)
            try:real_run(key,project,full)
            finally:done.set()
        with patch.object(self.service,'_run',delayed):
            first=self.service.start('p0');self.assertTrue(entered.wait(2))
            second=self.service.start('p0')
            self.assertIn('id',first);self.assertNotIn('id',second)
            release.set();self.assertTrue(done.wait(3))
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM libtv_sync_runs').fetchone()[0],1)

    def test_status_uses_recent_browser_heartbeat_and_explicit_force(self):
        self.store.context({'project':'p0','client':'tab','name':'项目','selected':[],'url':'https://www.liblib.tv/canvas?projectId=p0'})
        self.assertEqual(self.service.connections('p0')['browser']['status'],'connected')
        with self.store.connect() as c:c.execute('UPDATE contexts SET updated=?',(time.time()-40,))
        self.assertEqual(self.service.connections('p0')['browser']['status'],'disconnected')
        done=threading.Event()
        with patch.object(self.bridge,'check',side_effect=lambda:done.set()):
            self.service.connections('p0',force=True);self.assertTrue(done.wait(2))
        # The tiny background account write must end before the temporary store is removed.
        with self.service.check_lock:pass

    def test_paid_tools_rejected_before_connecting(self):
        bridge=CodexBridge(self.root)
        with patch.object(bridge,'connect') as connect:
            for tool in ('generate_video','create_image','update_node','arbitrary'):
                with self.assertRaises(ValueError):bridge.call(tool,{})
            connect.assert_not_called()


if __name__=='__main__':unittest.main()
