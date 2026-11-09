import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from extension_release import FILES, publish, installed_release


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.target, self.state = [self.root / p for p in ('source', 'target', 'state')]
        self.source.mkdir()
        for f in FILES: (self.source / f).write_bytes((ROOT / 'assets/companion/extension' / f).read_bytes())

    def tearDown(self): self.tmp.cleanup()

    def test_publish_complete_repeatable_package_and_backup(self):
        first = publish(self.source, self.target, self.state)
        self.assertTrue(first['ready'])
        self.assertEqual(first, installed_release(self.state))
        again = publish(self.source, self.target, self.state)
        self.assertEqual(first['buildId'], again['buildId'])
        self.assertTrue(list((self.state / 'extension-backups').glob('*/manifest.json')))
        (self.source / 'content.js').write_text('/* different tested build */')
        next_release = publish(self.source, self.target, self.state)
        self.assertNotEqual(first['buildId'], next_release['buildId'])
        self.assertEqual(first['version'], next_release['version'])

    def test_partial_or_tampered_deployment_cannot_trigger_update(self):
        self.assertFalse(installed_release(self.state)['ready'])
        publish(self.source, self.target, self.state)
        (self.target / 'content.js').write_text('partial')
        self.assertFalse(installed_release(self.state)['ready'])
        publish(self.source, self.target, self.state)
        (self.target / 'updater.js').unlink()
        self.assertFalse(installed_release(self.state)['ready'])

    def test_unknown_target_not_overwritten_and_no_arbitrary_paths(self):
        self.target.mkdir()
        (self.target / 'manifest.json').write_text('{"name":"Another extension"}')
        with self.assertRaises(ValueError): publish(self.source, self.target, self.state)
        (self.target / 'manifest.json').unlink()
        publish(self.source, self.target, self.state)
        path = self.target / 'release.json'; release = json.loads(path.read_text())
        release['files']['../secret'] = '0' * 64; path.write_text(json.dumps(release))
        self.assertFalse(installed_release(self.state)['ready'])


if __name__ == '__main__': unittest.main()
