#!/usr/bin/env python3
"""Copy an equal-length, same-speaker audio interval into a reviewed target.

No time stretch, voice synthesis or video re-encode. Boundary crossfades only.
The manifest records source hashes and original-time intervals.
"""
import argparse, array, hashlib, json, math, subprocess, wave
from pathlib import Path

def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def pcm(p):
    raw = subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-vn','-ar','48000','-ac','2','-f','s16le','-'])
    a = array.array('h'); a.frombytes(raw); return a

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('manifest',type=Path);ap.add_argument('output',type=Path)
    a=ap.parse_args();m=json.loads(a.manifest.read_text());p=Path(m['source']);out=a.output.resolve()
    if out.exists(): raise FileExistsError(out)
    if sha(p)!=m['sha256']: raise ValueError('Source changed')
    data=pcm(p); original=data[:]
    for e in m['patches']:
        src=Path(e.get('source',str(p)))
        if e.get('sha256') and sha(src)!=e['sha256']: raise ValueError('Patch source changed')
        source=original if src==p else pcm(src)
        sf=round(e['source_start']*48000);tf=round(e['target_start']*48000);count=round(e['duration']*48000)
        fade=min(round(e.get('crossfade_ms',6)*48),count//3)
        if min(sf,tf)<0 or count<=0 or (sf+count)*2>len(source) or (tf+count)*2>len(data): raise ValueError('Out of range')
        for i in range(count):
            weight=min(1.0,(i+1)/max(1,fade),(count-i)/max(1,fade))
            for c in range(2):
                at=(tf+i)*2+c;v=original[at]*(1-weight)+source[(sf+i)*2+c]*weight
                data[at]=max(-32768,min(32767,round(v)))
    out.parent.mkdir(parents=True,exist_ok=True);wav=out.with_suffix('.audio.wav')
    with wave.open(str(wav),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(data.tobytes())
    subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(p),'-i',str(wav),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(out)],check=True)
    result={'source':str(p),'source_sha256':sha(p),'output':str(out),'sha256':sha(out),'patches':m['patches'],'boundary':'Audio repair candidate; review actual word boundaries, timbre and lip sync before adoption.'}
    out.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
