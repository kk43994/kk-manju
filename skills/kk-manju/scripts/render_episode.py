#!/usr/bin/env python3
"""Render a reviewed frame-accurate cut manifest at original speed.

Source hashes and frame bounds are mandatory. Never pads or freezes pictures.
Only explicitly listed boundary focus/light effects are applied; native effects
are retained. Review decisions remain human/assistant evidence, not this script.
"""
import argparse, hashlib, json, subprocess
from pathlib import Path

def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def ff(*a):
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y',*map(str,a)],check=True)
def probe(p):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)]))

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('manifest',type=Path);ap.add_argument('output',type=Path)
    a=ap.parse_args();m=json.loads(a.manifest.read_text());out=a.output.resolve()
    if out.exists(): raise FileExistsError(out)
    fps=m['fps'];size=m['size']; clips=m['clips']; work=out.parent/(out.stem+'-parts')
    work.mkdir(parents=True,exist_ok=True);total=0;receipts=[]
    for i,c in enumerate(clips):
        p=Path(c['source']);meta=probe(p);v=next(s for s in meta['streams'] if s['codec_type']=='video')
        if sha(p)!=c['sha256']: raise ValueError('Source changed: '+str(p))
        if c['review_decision'] not in ('passed','user_accepted'): raise ValueError('Unreviewed input')
        if [v['width'],v['height']]!=size or v['avg_frame_rate']!=f'{fps}/1': raise ValueError('Unexpected input format')
        start,end=c['in_frame'],c['out_frame']; count=end-start;duration=count/fps
        if not 0<=start<end<=int(v['nb_frames']): raise ValueError('Out of source bounds')
        vf=f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,setsar=1,format=yuv420p'
        for side in ('in','out'):
            effect=c.get('effect_'+side,{})
            if effect.get('focus_frames'):
                n=effect['focus_frames']
                if n*2>count: raise ValueError('Effect exceeds source interval')
                for k in range(n):
                    frame=k if side=='in' else count-n+k
                    sigma=1.25*((n-k)/n if side=='in' else (k+1)/n)
                    vf+=f",gblur=sigma={sigma:.4f}:enable='between(n,{frame},{frame})'"
            if effect.get('brightness'):
                n=effect.get('frames',3);d=n/fps;level=effect['brightness']
                expr=f'if(lt(t,{d}),{level}*(1-t/{d}),0)' if side=='in' else f'if(gte(t,{duration-d}),{level}*(t-{duration-d})/{d},0)'
                vf+=f",eq=brightness='{expr}':eval=frame"
        if c.get('fade_out_frames'):
            d=c['fade_out_frames']/fps;vf+=f',fade=t=out:st={duration-d}:d={d}'
        # Only millisecond de-clicking. No pace change, freeze padding, music or subtitles.
        fade_in=c.get('audio_fade_in',0.012);fade_out=c.get('audio_fade_out',0.02)
        af=f'atrim=start={start/fps}:end={end/fps},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo'
        af+=f',afade=t=in:st=0:d={fade_in},afade=t=out:st={duration-fade_out}:d={fade_out}'
        part=work/f'{i:02}.mov'
        ff('-i',p,'-vf',vf,'-af',af,'-c:v','libx264','-threads','3','-preset','fast','-crf','16','-fps_mode','passthrough','-video_track_timescale',str(fps*1000),'-c:a','pcm_s16le',part)
        vi=next(s for s in probe(part)['streams'] if s['codec_type']=='video')
        if int(vi['nb_frames'])!=count: raise ValueError('Render changed frame count')
        receipts.append({**c,'output_start_frame':total,'output_end_frame':total+count,'part':str(part)})
        total+=count;print(c['id']+' rendered',flush=True)
    listing=work/'concat.txt';listing.write_text(''.join(f'file {i:02}.mov\n' for i in range(len(clips))))
    ff('-f','concat','-safe','1','-i',listing,'-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',out)
    result={'output':str(out),'sha256':sha(out),'bytes':out.stat().st_size,'frames':total,'fps':fps,'duration':total/fps,'size':size,'clips':receipts,'boundary':'Render receipt only; run export_verify and inspect final cut boundaries.'}
    out.with_suffix('.render.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='clips'},ensure_ascii=False))

if __name__=='__main__':main()
