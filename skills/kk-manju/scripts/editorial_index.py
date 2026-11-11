#!/usr/bin/env python3
"""Read-only source-shot indexing and OTIO interchange for KK edit plans.

Optional dependencies: scenedetect, opencv-python, opentimelineio.
Detections are review candidates, never automatic cuts. No media is altered.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.metadata
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_inputs(path):
    rows = json.loads(Path(path).read_text())['inputs']
    sources = {}
    for row in rows:
        key = row['candidate']
        if key in sources and row != sources[key]:
            raise ValueError(f'Conflicting source identity: {key}')
        sources[key] = row
    return sources


def index_source(row):
    import cv2
    from scenedetect import detect
    from scenedetect.detectors import AdaptiveDetector
    cv2.setNumThreads(1)
    path = Path(row['path'])
    actual = digest(path)
    if row.get('sha256') and actual != row['sha256']:
        raise ValueError(f'Source changed: {path}')
    scenes = detect(str(path), AdaptiveDetector(min_scene_len=2), show_progress=False,
                    start_in_scene=True)
    return {'candidate': row['candidate'], 'path': str(path.resolve()), 'sha256': actual,
            'detector': 'PySceneDetect.AdaptiveDetector(min_scene_len=2)',
            'review_status': 'unreviewed',
            'scenes': [{'source_in_s': a.seconds, 'source_out_s': b.seconds,
                        'in_frame': a.frame_num, 'out_frame': b.frame_num,
                        'fps': float(a.frame_rate)} for a, b in scenes]}


def index(args):
    plan = json.loads(Path(args.plan).read_text())
    inputs = read_inputs(args.input)
    wanted = {r['candidate'] for r in plan['picture']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(index_source, [inputs[k] for k in sorted(wanted)]))
    by_id = {r['candidate']: r for r in results}
    used = []
    for cut in plan['picture']:
        start = cut['source_start_ms'] / 1000
        end = start + cut['duration_ms'] / 1000
        pieces = []
        for shot in by_id[cut['candidate']]['scenes']:
            a, b = max(start, shot['source_in_s']), min(end, shot['source_out_s'])
            if b > a:
                pieces.append({'source_in_s': a, 'source_out_s': b,
                               'timeline_in_s': cut['start_ms']/1000+a-start,
                               'duration_s': b-a, 'short_fragment_candidate': b-a <= .5})
        used.append({'cut_id': cut['id'], 'candidate': cut['candidate'], 'subshots': pieces})
    report = {'schema': 1, 'purpose': 'locate internal cuts; no automatic editorial decision',
              'plan_sha256': digest(args.plan), 'input_sha256': digest(args.input),
              'versions': {p: importlib.metadata.version(p) for p in ['scenedetect', 'opencv-python']},
              'sources': results, 'used_subshots': used}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'sources': len(results), 'source_shots': sum(len(r['scenes']) for r in results),
                      'short_fragment_candidates': sum(s['short_fragment_candidate'] for r in used for s in r['subshots']),
                      'out': str(Path(args.out).resolve())}))


def export_otio(args):
    import opentimelineio as otio
    plan = json.loads(Path(args.plan).read_text())
    sources = read_inputs(args.input)
    # Millisecond rational time preserves existing source coordinates exactly.
    rt = lambda v: otio.opentime.RationalTime(v, 1000)
    tr = lambda a, d: otio.opentime.TimeRange(rt(a), rt(d))
    timeline = otio.schema.Timeline(name=Path(args.plan).parent.name)
    timeline.metadata['kk'] = {'plan_sha256': digest(args.plan), 'input_sha256': digest(args.input),
                               'render_properties_in_metadata_only': True,
                               'captions': plan.get('captions', []), 'effects': plan.get('effects', [])}
    for key, kind in [('picture', otio.schema.TrackKind.Video), ('audio', otio.schema.TrackKind.Audio)]:
        lanes = []
        for row in sorted(plan.get(key, []), key=lambda r: (r['start_ms'], r['id'])):
            source = sources[row['candidate']]
            if not Path(source['path']).is_file():
                raise FileNotFoundError(source['path'])
            if min(row['source_start_ms'], row['start_ms']) < 0 or row['duration_ms'] <= 0:
                raise ValueError(f'Invalid timing: {row["id"]}')
            # Allocate overlaps to independent tracks; never serialise overlapping audio.
            lane = next((i for i, (_, end) in enumerate(lanes) if end <= row['start_ms']), None)
            if lane is None:
                lane = len(lanes)
                lanes.append((otio.schema.Track(name=f'{key}-{lane+1}', kind=kind), 0))
            track, end = lanes[lane]
            if row['start_ms'] > end:
                track.append(otio.schema.Gap(source_range=tr(0, row['start_ms']-end)))
            clip = otio.schema.Clip(name=row['id'],
                media_reference=otio.schema.ExternalReference(target_url=Path(source['path']).resolve().as_uri()),
                source_range=tr(row['source_start_ms'], row['duration_ms']))
            clip.metadata['kk'] = {'edit': row, 'source_sha256': source.get('sha256'),
                                   'candidate': row['candidate']}
            track.append(clip)
            lanes[lane] = (track, row['start_ms']+row['duration_ms'])
        for track, _ in lanes:
            timeline.tracks.append(track)
    otio.adapters.write_to_file(timeline, args.out)
    reread = otio.adapters.read_from_file(args.out)
    expected = {r['id']: r for key in ['picture', 'audio'] for r in plan.get(key, [])}
    actual = {c.name: c for c in reread.find_clips()}
    if len(expected) != sum(len(plan.get(k, [])) for k in ['picture','audio']):
        raise ValueError('Duplicate clip IDs across tracks')
    assert expected.keys() == actual.keys()
    for key, row in expected.items():
        clip = actual[key]
        assert abs(clip.source_range.start_time.to_seconds()*1000-row['source_start_ms']) < .001
        assert abs(clip.source_range.duration.to_seconds()*1000-row['duration_ms']) < .001
        assert abs(clip.range_in_parent().start_time.to_seconds()*1000-row['start_ms']) < .001
    print(json.dumps({'out': str(Path(args.out).resolve()), 'clips': len(actual),
                      'tracks': len(reread.tracks), 'timing_roundtrip': 'passed',
                      'note': 'OTIO is an interchange backup; not a CapCut import or rendered effects proof'}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name, fn in [('index', index), ('otio', export_otio)]:
        q = sub.add_parser(name)
        q.add_argument('--plan', required=True)
        q.add_argument('--input', required=True)
        q.add_argument('--out', required=True)
        q.set_defaults(func=fn)
        if name == 'index':
            q.add_argument('--workers', type=int, default=2, choices=range(1,5))
    args = p.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == '__main__':
    main()
