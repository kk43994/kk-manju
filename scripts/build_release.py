#!/usr/bin/env python3
"""Build portable skill and browser ZIPs from the repository's canonical files."""
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'kk-manju'
EXTENSION = SKILL / 'assets' / 'companion' / 'extension'
EXCLUDE = {'__pycache__', 'backups', '.DS_Store', 'install-receipt.json', 'pairing-token'}


def package(source, target, prefix):
    files = sorted(p for p in source.rglob('*') if p.is_file()
                   and not EXCLUDE.intersection(p.relative_to(source).parts)
                   and p.suffix != '.pyc' and '.sqlite3' not in p.name)
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, str(Path(prefix) / path.relative_to(source)))
    with ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
    return {'file': target.name, 'size': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': len(files)}


def main():
    version = re.search(r'^  version: "([^"]+)"$', (SKILL / 'SKILL.md').read_text(), re.M).group(1)
    manifest = json.loads((EXTENSION / 'manifest.json').read_text())
    release = json.loads((EXTENSION / 'release.json').read_text())
    if release['version'] != manifest['version']:
        raise ValueError('Browser version mismatch')
    for name, expected in release['files'].items():
        path = EXTENSION / name
        if path.parent != EXTENSION or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Browser package hash mismatch: ' + name)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    packages = [package(SKILL, output / f'KK-Manju-Skill-{version}.zip', 'kk-manju'),
                package(EXTENSION, output / f'KK-Manju-Browser-{manifest["version"]}.zip', 'KK-Manju-Browser')]
    (output / 'SHA256SUMS.txt').write_text(''.join(f'{p["sha256"]}  {p["file"]}\n' for p in packages))
    metadata = {'skillVersion': version, 'browserVersion': manifest['version'], 'packages': packages}
    (output / 'release.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
