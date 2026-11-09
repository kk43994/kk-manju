#!/usr/bin/env python3
"""Render an explicit original-speed picture/audio EDL. No generation or auto review.

All coordinates are frames at manifest fps. Picture intervals may overlap only
through named transitions; audio intervals are independent for J/L cuts. Sources
are hash-checked. Dialogue guards prohibit boundary trims or fades over speech.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def digest(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def probe(p):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(p)]))


def render(manifest, output):
    m = json.loads(Path(manifest).read_text())
    out = Path(output).resolve()
    if out.exists():
        raise FileExistsError(out)
    fps, (w, h) = m['fps'], m['size']
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-filter_complex_threads', '1']
    indices, metadata = {}, {}
    for key, src in m['sources'].items():
        if digest(src['path']) != src['sha256']:
            raise ValueError('Changed source: ' + key)
        p = probe(src['path'])
        v = next(s for s in p['streams'] if s['codec_type'] == 'video')
        if [v['width'], v['height']] != [w, h] or v['avg_frame_rate'] != f'{fps}/1':
            raise ValueError('Source format differs: ' + key)
        metadata[key] = p
        indices[key] = len(indices)
        cmd += ['-threads', '1', '-i', src['path']]
    filters, timeline, total, last = [], [], 0, None
    for i, p in enumerate(m['picture']):
        start, end = p['in_frame'], p['out_frame']
        v = next(s for s in metadata[p['source']]['streams'] if s['codec_type'] == 'video')
        if not 0 <= start < end <= int(v['nb_frames']):
            raise ValueError('Picture out of bounds: ' + p['id'])
        overlap = p.get('overlap_frames', 0)
        if overlap < 0 or overlap >= min(end-start, total if i else end-start) or (i == 0 and overlap):
            raise ValueError('Invalid overlap')
        f = f"[{indices[p['source']]}:v]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,setsar=1,fps={fps},settb=AVTB,format=yuv420p"
        if p.get('crop'):
            cw, ch, x, y = p['crop']
            if min(x,y) < 0 or x+cw > w or y+ch > h:
                raise ValueError('Crop out of bounds')
            f += f',crop={cw}:{ch}:{x}:{y},scale={w}:{h}:flags=lanczos,setsar=1'
        label = f'v{i}'
        filters.append(f + f'[{label}]')
        timeline.append({**p, 'at_frame': total-overlap, 'end_frame': total-overlap+end-start})
        if last is None:
            last = label
        else:
            new = f'join{i}'
            if overlap:
                transition = p['transition']
                if transition not in ('fade', 'fadeblack', 'fadewhite', 'hblur', 'smoothleft', 'smoothright'):
                    raise ValueError('Unsupported transition')
                filters.append(f'[{last}][{label}]xfade=transition={transition}:duration={overlap/fps:.9f}:offset={(total-overlap)/fps:.9f}[{new}]')
            else:
                filters.append(f'[{last}][{label}]concat=n=2:v=1:a=0,settb=AVTB[{new}]')
            last = new
        total += end-start-overlap
    tail = m.get('black_tail_frames', 0)
    if tail:
        filters.append(f'color=black:s={w}x{h}:r={fps}:d={tail/fps:.9f},settb=AVTB[black]')
        filters.append(f'[{last}][black]concat=n=2:v=1:a=0,settb=AVTB[withblack]')
        last = 'withblack'
        total += tail
    alabels = []
    for i, a in enumerate(m['audio']):
        st, en, at = a['in_frame']/fps, a['out_frame']/fps, a['at_frame']/fps
        dur, fi, fo = en-st, a.get('fade_in_seconds', 0), a.get('fade_out_seconds', 0)
        available = float(next(s for s in metadata[a['source']]['streams'] if s['codec_type']=='audio')['duration'])
        if not (0 <= st < en <= available + .001 and at >= 0 and fi+fo <= dur):
            raise ValueError('Audio bounds: ' + a['id'])
        for gs, ge in a.get('speech_guards', []):
            if gs < st-0.001 or ge > en+0.001 or gs < st+fi-.001 or ge > en-fo+.001:
                raise ValueError('Audio trim/fade intersects protected speech: ' + a['id'])
        f = f"[{indices[a['source']]}:a]atrim=start={st:.9f}:end={en:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if fi: f += f',afade=t=in:st=0:d={fi:.9f}:curve=qsin'
        if fo: f += f',afade=t=out:st={dur-fo:.9f}:d={fo:.9f}:curve=qsin'
        if a.get('gain_db',0): f += f",volume={a['gain_db']}dB"
        f += f',adelay={round(at*48000)}S:all=1[a{i}]'
        filters.append(f)
        alabels.append(f'[a{i}]')
    # Explicit trailing silence permits a deliberate black tail, never freezes video.
    filters.append(''.join(alabels)+f'amix=inputs={len(alabels)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={total/fps:.9f},atrim=end={total/fps:.9f}[audio]')
    filters.append(f'[{last}]fps={fps},trim=end_frame={total},setpts=N/({fps}*TB),format=yuv420p[video]')
    graph = out.with_suffix('.ffgraph')
    graph.write_text(';\n'.join(filters))
    partial = out.with_name(out.stem+'.rendering.mp4')
    cmd += ['-filter_complex_script', str(graph), '-map', '[video]', '-map', '[audio]',
            '-c:v', 'libx264', '-threads', '3', '-preset', 'fast', '-crf', '16',
            '-fps_mode', 'passthrough', '-video_track_timescale', str(fps*1000),
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(partial)]
    subprocess.run(cmd, check=True)
    actual = probe(partial)
    v = next(s for s in actual['streams'] if s['codec_type']=='video')
    if int(v['nb_frames']) != total:
        raise RuntimeError(f'Frame mismatch: expected {total}, got {v["nb_frames"]}')
    partial.replace(out)
    receipt = {'output':str(out), 'sha256':digest(out), 'manifest_sha256':digest(manifest),
               'frames':total, 'fps':fps, 'duration':total/fps, 'picture':timeline,
               'audio':m['audio'], 'review':'rendered; requires actual seam and voice review'}
    out.with_suffix('.render.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('picture','audio')},ensure_ascii=False))


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest',type=Path); ap.add_argument('output',type=Path)
    args=ap.parse_args(); render(args.manifest,args.output)
