#!/usr/bin/env python3
"""Burn an independently saved caption layer with a real local font.

Requires Pillow. Uses a small transparent RGBA strip because some FFmpeg builds
have no libass. Source audio packets are copied; no model text or timing guesses.
"""
import argparse, hashlib, json, math, re, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter


def stamp(t):
    ms=round(t*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path);ap.add_argument('cues',type=Path)
    ap.add_argument('style',type=Path);ap.add_argument('output',type=Path)
    a=ap.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(a.source)]))
    v=next(s for s in meta['streams'] if s['codec_type']=='video');w,h=v['width'],v['height']
    n,d=map(int,v['avg_frame_rate'].split('/'));fps=n/d;frames=int(v['nb_frames'])
    cues=json.loads(a.cues.read_text());style=json.loads(a.style.read_text())
    if 'subtitle' in style:style=style['subtitle']
    font_path=Path(style['font_file']);scale=4;strip_h=100
    font=ImageFont.truetype(str(font_path),round(style['font_size_at_480p']*h/480*scale))
    margin=round(style['bottom_margin_at_480p']*h/480*scale)
    image_dir=a.output.parent/(a.output.stem+'-字幕图层');image_dir.mkdir(exist_ok=True)
    blank=Image.new('RGBA',(w,strip_h),(0,0,0,0)).tobytes();layers=[];last=0;display_cues=[]
    for i,c in enumerate(cues):
        if c['start'] < last-1e-6 or not 0 <= c['start'] < c['end'] <= frames/fps+.001:
            raise ValueError('Caption overlap or range error')
        last=c['end'];text=c['text']
        if not style.get('punctuation',True):text=re.sub(r'[，。！？、；：…“”‘’,.!?;:]+','',text)
        display_cues.append({**c, 'text':text})
        im=Image.new('RGBA',(w*scale,strip_h*scale),(0,0,0,0));dr=ImageDraw.Draw(im)
        box=dr.textbbox((0,0),text,font=font)
        if box[2]-box[0]>w*scale*.90:raise ValueError('Caption too wide; split the cue: '+text)
        x=(w*scale-(box[2]-box[0]))/2-box[0];y=strip_h*scale-margin-box[3]
        shadow=Image.new('RGBA',im.size);sd=ImageDraw.Draw(shadow)
        sd.text((x+1,y+2),text,font=font,fill=(0,0,0,130),stroke_width=3,stroke_fill=(0,0,0,110))
        shadow=shadow.filter(ImageFilter.GaussianBlur(2));im.alpha_composite(shadow)
        dr=ImageDraw.Draw(im);dr.text((x,y),text,font=font,fill='white',stroke_width=1,stroke_fill=(0,0,0,130))
        im=im.resize((w,strip_h),Image.Resampling.LANCZOS);im.save(image_dir/f'{i+1:03}.png')
        layers.append((math.ceil(c['start']*fps-1e-7),math.ceil(c['end']*fps-1e-7),im.tobytes()))
    a.output.with_suffix('.srt').write_text('\n\n'.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(display_cues))+'\n')
    partial=a.output.with_name(a.output.stem+'.rendering.mp4')
    cmd=['ffmpeg','-nostdin','-v','error','-y','-threads','2','-i',str(a.source),'-f','rawvideo','-pixel_format','rgba','-video_size',f'{w}x{strip_h}','-framerate',f'{n}/{d}','-i','pipe:0','-filter_complex_threads','1','-filter_complex',f'[0:v][1:v]overlay=0:{h-strip_h}:shortest=1:format=auto,format=yuv420p[v]','-map','[v]','-map','0:a?','-c:v','libx264','-threads','3','-preset','fast','-crf','16','-c:a','copy','-frames:v',str(frames),'-fps_mode','passthrough','-movflags','+faststart',str(partial)]
    p=subprocess.Popen(cmd,stdin=subprocess.PIPE);idx=0
    try:
        for f in range(frames):
            while idx<len(layers) and f>=layers[idx][1]:idx+=1
            p.stdin.write(layers[idx][2] if idx<len(layers) and layers[idx][0]<=f<layers[idx][1] else blank)
        p.stdin.close()
        if p.wait():raise RuntimeError('Subtitle render failed')
    except Exception:
        p.kill();p.wait();raise
    def audio_hash(path):
        return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a','-c','copy','-f','hash','-hash','sha256','-']).decode().strip()
    if audio_hash(a.source)!=audio_hash(partial):raise RuntimeError('Audio packets changed')
    partial.replace(a.output)
    a.output.with_suffix('.caption-render.json').write_text(json.dumps({'source':str(a.source.resolve()),'output':str(a.output.resolve()),'font':str(font_path),'font_size':style['font_size_at_480p'],'bottom_margin':style['bottom_margin_at_480p'],'frames':frames,'cues':len(cues),'audio_packet_hash':audio_hash(a.output),'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest()},ensure_ascii=False,indent=2)+'\n')
    print(str(a.output))


if __name__=='__main__':main()
