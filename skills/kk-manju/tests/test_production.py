import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from companion_store import Store
import production_catalog as p
class ProductionTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'state');self.store.project('project','测试')
  movie=self.root/'movie.mp4';movie.write_bytes(b'fixture')
  self.data={'project':'project','episodes':[{'id':'EP001','versions':[{'id':'V3','shots':[{'id':'V09','adopted':None,'candidates':[{'id':'R1','path':str(movie)},{'id':'R2','path':str(movie)}]}]}]}],'assets':[]}
  p.import_catalog(self.store,self.data)
 def tearDown(self):self.tmp.cleanup()
 def request(self,**kw):return dict(id='req',project='project',episode='EP001',version='V3',shot='V09',candidate='R2',expectedRevision=0,**kw)
 def test_preview_read_never_adopts_or_exposes_local_paths(self):
  for _ in range(3):
   read=p.read(self.store,{'project':'project'});shot=read['episodes'][0]['versions'][0]['shots'][0]
   self.assertIsNone(shot['adopted']);self.assertNotIn('path',shot['candidates'][0])
 def test_explicit_adopt_idempotency_conflict_and_history(self):
  req=self.request();self.assertEqual(p.adopt(self.store,req),p.adopt(self.store,req))
  req['candidate']='R1'
  with self.assertRaises(ValueError):p.adopt(self.store,req)
  req['id']='req2'
  with self.assertRaises(ValueError):p.adopt(self.store,req)
  req['expectedRevision']=1;p.adopt(self.store,req)
  with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM production_actions').fetchone()[0],2)
  p.import_catalog(self.store,self.data)
  self.assertEqual(p.read(self.store,{'project':'project'})['episodes'][0]['versions'][0]['shots'][0]['adopted'],'R1')
 def test_changed_media_rejects_old_identity(self):
  import hashlib
  self.data['episodes'][0]['versions'][0]['shots'][0]['candidates'][1]['sha256']=hashlib.sha256(b'fixture').hexdigest()
  p.import_catalog(self.store,self.data)
  (self.root/'movie.mp4').write_bytes(b'changed')
  with self.assertRaises(ValueError):p.adopt(self.store,self.request())
  with self.assertRaises(ValueError):p.media(self.store,'project','R2')
 def test_cross_episode_and_project_rejected(self):
  for key,value in [('project','other'),('episode','EP002'),('version','V2'),('shot','V08'),('candidate','other')]:
   r=self.request();r[key]=value
   with self.assertRaises(ValueError):p.adopt(self.store,r)
 def test_missing_media_and_path_traversal(self):
  with self.assertRaises(ValueError):p.media(self.store,'project','../../pairing-token')
  (self.root/'movie.mp4').unlink()
  with self.assertRaises(ValueError):p.adopt(self.store,self.request())
if __name__=='__main__':unittest.main()
