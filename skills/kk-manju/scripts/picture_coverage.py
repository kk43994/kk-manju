#!/usr/bin/env python3
"""Replace an equal-length picture range; retain the exact original audio packets.

Manifest: source, source_sha256, coverage, coverage_sha256, fps,
start_frame, end_frame, coverage_start_frame (default 0).
All media must have matching dimensions and frame rate. No scaling or retiming.
"""
import argparse
import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def digest(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def probe(p):
    return json.loads(run(['ffprobe', '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(p)]))


def audio_hash(p):
    return run(['ffmpeg', '-v', 'error', '-i', str(p), '-map', '0:a:0', '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest'); ap.add_argument('output')
    a = ap.parse_args(); m = json.loads(Path(a.manifest).read_text())
    src, cov, out = Path(m['source']), Path(m['coverage']), Path(a.output)
    if out.exists():
        raise ValueError('Output already exists; preserve the prior version')
    for p, k in [(src, 'source_sha256'), (cov, 'coverage_sha256')]:
        if digest(p) != m[k]:
            raise ValueError('Source hash changed: ' + str(p))
    ps, pc = probe(src), probe(cov)
    vs = next(x for x in ps['streams'] if x['codec_type'] == 'video')
    vc = next(x for x in pc['streams'] if x['codec_type'] == 'video')
    fps = int(m['fps']); total = int(vs['nb_read_frames'])
    for v in [vs, vc]:
        if Fraction(v['avg_frame_rate']) != fps or (v['width'], v['height']) != (vs['width'], vs['height']):
            raise ValueError('Coverage dimensions or frame rate do not match')
    start, end = int(m['start_frame']), int(m['end_frame'])
    cstart = int(m.get('coverage_start_frame', 0)); length = end - start
    if not (0 <= start < end <= total and 0 <= cstart and cstart + length <= int(vc['nb_read_frames'])):
        raise ValueError('Invalid frame range')
    filters, labels = [], []
    if start:
        filters.append(f'[0:v]trim=start_frame=0:end_frame={start},setpts=PTS-STARTPTS[p]'); labels.append('[p]')
    filters.append(f'[1:v]trim=start_frame={cstart}:end_frame={cstart+length},setpts=PTS-STARTPTS[c]'); labels.append('[c]')
    if end < total:
        filters.append(f'[0:v]trim=start_frame={end}:end_frame={total},setpts=PTS-STARTPTS[s]'); labels.append('[s]')
    filters.append(''.join(labels) + f'concat=n={len(labels)}:v=1:a=0[out]')
    out.parent.mkdir(parents=True, exist_ok=True)
    run(['ffmpeg', '-v', 'error', '-n', '-i', str(src), '-i', str(cov), '-filter_complex', ';'.join(filters),
         '-map', '[out]', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'medium', '-crf', '17',
         '-threads', '3', '-pix_fmt', 'yuv420p', '-fps_mode', 'cfr', '-r', str(fps),
         '-video_track_timescale', str(fps*1000), '-c:a', 'copy', '-movflags', '+faststart', str(out)])
    vo = next(x for x in probe(out)['streams'] if x['codec_type'] == 'video')
    if int(vo['nb_read_frames']) != total or audio_hash(out) != audio_hash(src):
        raise ValueError('Output frame count or original audio verification failed')
    report = {'manifest': str(Path(a.manifest).resolve()), 'source_sha256': digest(src),
              'coverage_sha256': digest(cov), 'output': str(out.resolve()), 'sha256': digest(out),
              'frames': total, 'fps': fps, 'picture_replacement': [start, end],
              'original_audio_packet_hash_unchanged': True, 'audio_hash': audio_hash(out)}
    out.with_suffix('.coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
