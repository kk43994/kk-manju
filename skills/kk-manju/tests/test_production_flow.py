import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from companion_store import Store, sha
import production_catalog as catalog
import production_flow as flow


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = Store(self.root/'state'); self.store.project('p','test')
        self.media = self.root/'clip.mp4'; self.media.write_bytes(b'media')
        catalog.import_catalog(self.store, {'project':'p','episodes':[{'id':'E1','versions':[{'id':'V3','shots':[{'id':'V09','adopted':'old','candidates':[{'id':'old','path':str(self.media)}]}]}]}],'assets':[]})
        self.plan = self.write('plan.json', {'project_id':'p','revision':'r'})
        self.receipt = self.write('receipt.json', {'task_id':'task','outputs':[{'node_id':'node'}]})
        self.job = {'status':'succeeded','task_id':'task','outputs':[{'node_id':'node'}], 'review':'pending','receipts':[{'path':str(self.receipt),'sha256':sha(self.receipt)}]}
        self.state = self.write('pipeline.json', {'project_id':'p','revision':'r','jobs':{'V09R3':self.job}})
        self.config = {'id':'f','project':'p','episode':'E1','version':'V3','shot':'V09','job':'V09R3','label':'R3','plan':str(self.plan),'state':str(self.state)}
        flow.register(self.store,self.config)
    def write(self,name,data):
        p=self.root/name;p.write_text(json.dumps(data));return p
    def tearDown(self): self.temp.cleanup()
    def test_live_read_separates_receipts_and_review(self):
        result=flow.read(self.store,'p')[0]
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['review'],'pending')
        self.assertNotIn(str(self.root), json.dumps(result))
        self.receipt.write_text('tampered')
        self.assertEqual(flow.read(self.store,'p')[0]['status'],'unverified')
    def test_unknown_does_not_become_retry_or_new_job(self):
        self.job.update(status='unknown')
        self.write('pipeline.json',{'project_id':'p','revision':'r','jobs':{'V09R3':self.job}})
        for _ in range(3): self.assertEqual(flow.read(self.store,'p')[0]['status'],'unknown')
        self.assertEqual(len(flow.configurations(self.store,'p')),1)
    def test_registration_is_immutable_and_scoped(self):
        flow.register(self.store,self.config)
        with self.assertRaises(ValueError):flow.register(self.store,{**self.config,'project':'other'})
        self.plan.write_text(json.dumps({'project_id':'p','revision':'r','changed':True}))
        self.assertTrue(flow.read(self.store,'p')[0]['inputChanged'])
        with self.assertRaises(ValueError):flow.register(self.store,self.config)
    def test_ingest_requires_matching_decode_and_preserves_choice(self):
        manifest=self.write('delivery.json',{'files':[{'path':str(self.media),'nodeId':'node','sha256':sha(self.media)}]})
        report=self.write('verify.json',{'technical_status':'passed','sha256':'wrong','full_audio_video_decode_passed':True})
        with self.assertRaises(ValueError):flow.ingest(self.store,'f',manifest,report)
        self.write('verify.json',{'technical_status':'passed','sha256':sha(self.media),'full_audio_video_decode_passed':True})
        with patch.object(flow.subprocess,'run'),patch.object(flow.subprocess,'check_output',return_value=b'{"format":{"duration":"12"}}'):
            first=flow.ingest(self.store,'f',manifest,report);second=flow.ingest(self.store,'f',manifest,report)
        self.assertEqual(first,second)
        shot=catalog.raw(self.store,'p')['episodes'][0]['versions'][0]['shots'][0]
        self.assertEqual(shot['adopted'],'old');self.assertEqual(len(shot['candidates']),2)
        self.assertEqual(shot['candidates'][-1]['creative'],'pending')


if __name__=='__main__': unittest.main()
