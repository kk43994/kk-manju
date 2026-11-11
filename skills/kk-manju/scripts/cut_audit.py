#!/usr/bin/env python3
"""Find short shot fragments, including cuts INSIDE a generated source clip.

This is a review locator, not an aesthetic rule: an intentional montage may be
short, and scene detection also reacts to fast motion. Inspect flagged frames.
"""
import argparse
import concurrent.futures
import json
import re
import subprocess
from pathlib import Path
from companion_store import sha
from capcut_edit import digest, save


def short_fragments(plan, source_cuts, threshold_ms=500):
    findings = []
    for clip in plan['picture']:
        left = clip.get('source_start_ms', 0)
        right = left + clip['duration_ms']
        # Ignore sub-ms artifacts of the integer-ms draft protocol. A real
        # one-frame residual at 24fps lasts ~41.67ms and must remain visible.
        cuts = [t * 1000 for t in source_cuts[clip['candidate']] if left + 1 < t * 1000 < right - 1]
        boundaries = [left, *cuts, right]
        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            if end - start > threshold_ms:
                continue
            item = {'edit_id': clip['id'], 'candidate': clip['candidate'],
                'source_start_ms': round(start, 3), 'source_end_ms': round(end, 3),
                'duration_ms': round(end - start, 3),
                'project_start_ms': round(clip['start_ms'] + start - left, 3),
                'position': 'tail' if i == len(boundaries) - 2 else 'head' if i == 0 else 'internal'}
            item.update(id=digest(item)[:20], review='pending', note='')
            findings.append(item)
    return findings


def validate_review(plan, report):
    if report.get('plan_digest') != digest(plan):
        raise ValueError('切口检查对应旧剪辑单，请按当前版本重新检查')
    if any(x.get('review') not in ('intentional', 'false_positive', 'repaired') or not x.get('note', '').strip()
           for x in report.get('findings', [])):
        raise ValueError('仍有未经逐帧核对的短镜头残片')


def scan(plan, inputs):
    if plan['selection_digest'] != inputs['selection_digest']:
        raise ValueError('切口检查的采用来源与剪辑单不一致')

    def one(row):
        if sha(row['path']) != row['sha256']:
            raise ValueError('源媒体内容已改变')
        result = subprocess.run(['ffmpeg', '-hide_banner', '-i', row['path'], '-an', '-vf',
            "select='gt(scene,0.22)',showinfo", '-fps_mode', 'vfr', '-f', 'null', '-'],
            capture_output=True, text=True, check=True, timeout=180)
        return row['candidate'], [float(t) for t in re.findall(r'pts_time:([\d.]+)', result.stderr)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        # Sound-only stems have no visual cut points; still validate the whole
        # selection in capcut_edit.validate_inputs before publishing.
        used = {row['candidate'] for row in plan['picture']}
        cuts = dict(pool.map(one, [row for row in inputs['inputs'] if row['candidate'] in used]))
    return {'plan_digest': digest(plan), 'selection_digest': plan['selection_digest'],
            'threshold_ms': 500, 'source_cuts': cuts, 'findings': short_fragments(plan, cuts),
            'scope': 'scene-change locator plus retained edit intervals; manual frame review required'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'input', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    result = scan(json.loads(args.plan.read_text()), json.loads(args.input.read_text()))
    save(args.out, result)
    print(json.dumps({'findings': len(result['findings']), 'review': 'pending' if result['findings'] else 'no_short_fragments_detected'}))


if __name__ == '__main__':
    main()
