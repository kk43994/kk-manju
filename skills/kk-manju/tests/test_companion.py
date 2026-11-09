import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from companion import handler_factory, token_for
from companion_store import Store, clean


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'state')
        self.skill = Path(self.tmp.name) / 'skill'
        (self.skill / 'scripts').mkdir(parents=True)
        self.token = token_for(self.store)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(self.store, self.skill, ROOT / 'assets/companion/web', self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def http(self, path, data=None, auth=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if auth: headers['Authorization'] = 'Bearer ' + self.token
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def context(self, **changes):
        return dict(project='p1', client='tab-1', name='测试项目', selected=['v-a'], url='https://www.liblib.tv/canvas?projectId=p1', **changes)

    def test_http_boundaries(self):
        self.assertEqual(self.http('/api/state', auth=False)[0], 401)
        self.assertEqual(self.http('/api/state', origin='https://evil.example')[0], 403)
        self.assertEqual(self.http('/api/run', {'script': 'rm'})[0], 404)
        self.assertEqual(self.http('/../pairing-token')[0], 404)
        self.assertEqual(self.http('/api/state', origin='chrome-extension://' + 'a'*32)[0], 200)
        self.assertEqual(os.stat(self.store.directory / 'pairing-token').st_mode & 0o777, 0o600)
        self.assertEqual(self.http('/api/extension-release', auth=False)[0], 401)
        self.assertEqual(self.http('/api/extension-release', origin='https://evil.example')[0], 403)
        self.assertEqual(json.loads(self.http('/api/extension-release')[1])['ready'], False)
        for path in ('/api/connections','/api/libtv/sync','/api/libtv/catalog'):
            self.assertEqual(self.http(path,{'project':'p1'},auth=False)[0],401)
            self.assertEqual(self.http(path,{'project':'p1'},origin='https://evil.example')[0],401)
        self.assertEqual(self.http('/api/libtv/tool',{'tool':'generate_video','project':'p1'})[0],404)
        self.assertEqual(self.http('/api/libtv/media?project=p1&id=../../pairing-token')[0],404)

    def test_running_extension_version_preserved_and_validated(self):
        data = self.context(extension={'version':'0.3.0','buildId':'a'*64})
        self.assertEqual(self.http('/api/context', data)[0], 200)
        self.assertEqual(self.store.state(self.skill,'p1')['contexts'][0]['extension'], data['extension'])
        data['extension']['version'] = '<script>'
        self.assertEqual(self.http('/api/context', data)[0], 400)

    def test_context_is_project_scoped(self):
        self.assertEqual(self.http('/api/context', self.context())[0], 200)
        bad = self.context(); bad['project'] = 'p2'
        self.assertEqual(self.http('/api/context', bad)[0], 400)
        request = dict(id='c1', project='p2', client='tab-1', kind='organize_selection')
        self.assertEqual(self.http('/api/commands', request)[0], 400)

    def test_issue_retry_and_conflict(self):
        issue = dict(id='i1', project='p1', node='v1', timecode=4.5, category='声音', description='句尾被截断')
        self.assertEqual(self.http('/api/issues', issue)[0], 200)
        self.assertEqual(self.http('/api/issues', issue)[0], 200)
        self.assertEqual(len(self.store.state(self.skill, 'p1')['issues']), 1)
        issue['description'] = '别的问题'
        self.assertEqual(self.http('/api/issues', issue)[0], 400)
        issue['id'] = 'i2'; issue['timecode'] = float('nan')
        self.assertEqual(self.http('/api/issues', issue)[0], 400)

    def test_dom_cannot_complete_platform_request(self):
        self.store.context(self.context())
        request = dict(id='c1', project='p1', client='tab-1', kind='native_arrange')
        self.store.command(request)
        self.store.context(self.context())
        self.store.command(request)  # heartbeats do not break idempotency
        self.store.command_observed({'id': 'c1', 'project': 'p1', 'client': 'tab-1', 'note': '已观察到位置变化'})
        self.assertEqual(self.store.state(self.skill, 'p1')['commands'][0]['status'], 'awaiting_readback')
        with self.assertRaises(ValueError): self.store.finish_command('c1', {'project':'p2'})
        self.store.finish_command('c1', {'project':'p1','tool':'read_project','readback':{'edgesPreserved':True},'evidence':'/evidence.json'})
        self.assertEqual(self.store.state(self.skill, 'p1')['commands'][0]['status'], 'verified')

    def test_concurrent_events_and_stale_runs(self):
        run = self.store.start_run('p1', 'media_probe', '抽帧', 'abc')
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda n: self.store.event('p1', 'log', str(n), run), range(50)))
        events = self.store.state(self.skill, 'p1')['events']
        self.assertEqual(len({x['seq'] for x in events}), 51)
        with self.store.connect() as c: c.execute('UPDATE runs SET heartbeat=? WHERE id=?', (time.time()-60, run))
        self.assertEqual(self.store.state(self.skill, 'p1')['runs'][0]['status'], 'unknown')
        self.assertEqual(self.store.state(self.skill, 'different')['runs'], [])

    def test_runner_actual_exit_and_artifact(self):
        script = self.skill / 'scripts/media_probe.py'
        script.write_text("import sys\nprint('token=private-value')\nsys.exit(int(sys.argv[1]))\n")
        cmd = [sys.executable, str(ROOT/'scripts/companion.py'), '--state-dir', str(self.store.directory), '--skill-root', str(self.skill), 'run', '--project', 'p1', '--script', 'media_probe', '--']
        self.assertEqual(subprocess.run(cmd+['0'], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(cmd+['7'], capture_output=True).returncode, 7)
        state = self.store.state(self.skill, 'p1')
        self.assertEqual([r['status'] for r in state['runs']], ['failed','completed'])
        self.assertNotIn('private-value', json.dumps(state))
        self.assertEqual(state['runs'][0]['exit_code'], 7)

    def test_log_redaction(self):
        text = clean('Authorization=secret token=xxx https://x.test/a?signed=123#frag Bearer abc-123')
        for secret in ['=secret', '=xxx', 'signed=123', 'abc-123']: self.assertNotIn(secret, text)

    def test_batch_name_and_selection_are_idempotent(self):
        self.store.context(self.context())
        req = dict(id='batch1', project='p1', client='tab-1', kind='organize_selection', selected=['v-a'], batch='V3｜试做 R2')
        self.assertEqual(self.http('/api/commands', req)[0], 200)
        self.assertEqual(self.http('/api/commands', req)[0], 200)
        req['batch'] = 'V4｜试做 R1'
        self.assertEqual(self.http('/api/commands', req)[0], 400)
        req['id'] = 'batch2'; req['batch'] = ''
        self.assertEqual(self.http('/api/commands', req)[0], 400)
        req['batch'] = 'R1'; req['selected'] = ['other']
        self.assertEqual(self.http('/api/commands', req)[0], 400)

    def test_batch_directory_and_observation_isolation(self):
        self.store.context(self.context())
        self.store.project('p1', snapshot={'batches':[{'id':'g1','name':'R1','count':1}]})
        self.assertEqual(len(json.loads(self.http('/api/batches', {'project':'p1','client':'tab-1'})[1])['batches']), 1)
        self.assertEqual(self.http('/api/batches', {'project':'p2','client':'tab-1'})[0], 400)
        self.store.command(dict(id='native1', project='p1', client='tab-1', kind='native_group_layout'))
        self.assertEqual(self.http('/api/commands/observed', dict(id='native1', project='p2', client='tab-1', note='位置变化'))[0], 400)
        self.assertEqual(self.http('/api/commands/observed', dict(id='native1', project='p1', client='tab-2', note='位置变化'))[0], 400)
        self.assertEqual(self.http('/api/commands/observed', dict(id='native1', project='p1', client='tab-1', note='位置变化'))[0], 200)


if __name__ == '__main__': unittest.main()
