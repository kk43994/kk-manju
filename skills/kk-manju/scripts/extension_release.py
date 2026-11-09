"""Publish and verify the local unpacked extension. Never serves executable code."""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILES = ('manifest.json', 'adapter.js', 'content.js', 'background.js', 'updater.js',
         'production.html', 'production.js', 'production.css', 'logo.svg', 'options.html', 'options.css', 'options.js')
SCHEMA = 1


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def publish(source, target, state):
    source, target, state = (Path(p).expanduser().resolve() for p in (source, target, state))
    if source == target: raise ValueError('发布源和已加载目录必须分开')
    blobs = {f: (source / f).read_bytes() for f in FILES}
    manifest = json.loads(blobs['manifest.json'])
    if manifest.get('name') != 'KK 漫剧 · LibTV 制作伴随面板': raise ValueError('扩展身份不符')
    if (target / 'manifest.json').exists():
        old = json.loads((target / 'manifest.json').read_bytes())
        if old.get('name') != manifest['name']: raise ValueError('目标是其他扩展，拒绝覆盖')
    build = digest(encoded({f: digest(b) for f, b in blobs.items()}))
    identity = {'schema': SCHEMA, 'version': manifest['version'], 'buildId': build}
    blobs['build-info.js'] = ('var KK_BUILD = Object.freeze(' + json.dumps(identity) + ');\n').encode()
    release = {**identity, 'publishedAt': datetime.now(timezone.utc).isoformat(),
               'files': {f: digest(b) for f, b in blobs.items()}}
    # Publish only complete packages. The service independently verifies every file.
    state.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = state / 'extension-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup.mkdir(parents=True)
        for f in (*FILES, 'build-info.js', 'release.json'):
            if (target / f).is_file(): shutil.copy2(target / f, backup / f)
    for f, b in blobs.items(): atomic(target / f, b)
    atomic(target / 'release.json', encoded(release))
    # Also make direct development-folder loading use the same build identity.
    atomic(source / 'build-info.js', blobs['build-info.js'])
    atomic(source / 'release.json', encoded(release))
    atomic(state / 'extension-release.json', encoded({'directory': str(target), 'buildId': build}))
    return installed_release(state)


def installed_release(state):
    """Return metadata only; incomplete/modified deployments never trigger reload."""
    try:
        pointer = json.loads((Path(state) / 'extension-release.json').read_bytes())
        target = Path(pointer['directory'])
        release = json.loads((target / 'release.json').read_bytes())
        files = release['files']
        if release['schema'] != SCHEMA or release['buildId'] != pointer['buildId']:
            raise ValueError('发布版本不匹配')
        if set(files) != {*FILES, 'build-info.js'}: raise ValueError('文件清单不完整')
        if any(digest((target / f).read_bytes()) != files[f] for f in files):
            raise ValueError('文件校验失败')
        expected = digest(encoded({f: files[f] for f in FILES}))
        if expected != release['buildId']: raise ValueError('构建指纹不匹配')
        info = {k: release[k] for k in ('schema', 'version', 'buildId', 'publishedAt')}
        if json.loads((target / 'manifest.json').read_bytes())['version'] != info['version']:
            raise ValueError('版本号不匹配')
        return {**info, 'ready': True}
    except (OSError, ValueError, KeyError, TypeError):
        return {'schema': SCHEMA, 'ready': False, 'reason': '尚无完整且通过校验的本地扩展包'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--target', type=Path, default=Path.home() / 'Desktop/KK漫剧-LibTV扩展')
    p.add_argument('--state-dir', type=Path, default=Path.home() / '.local/share/kk-manju-companion')
    args = p.parse_args()
    print(json.dumps(publish(args.source, args.target, args.state_dir), ensure_ascii=False))


if __name__ == '__main__': main()
