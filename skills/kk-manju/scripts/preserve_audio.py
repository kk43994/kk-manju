"""Keep generated picture and original audio packets, with explicit provenance."""
import argparse, json, os, subprocess, tempfile
from pathlib import Path
from companion_store import sha


def stream_hash(path, stream):
    return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map',stream,'-c','copy','-f','hash','-hash','sha256','-'],text=True).strip()


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))['streams']


def preserve(picture, original, output, receipt):
    picture,original,output,receipt=map(lambda p:Path(p).resolve(),(picture,original,output,receipt))
    if output in (picture,original):raise ValueError('输出必须为新文件')
    identity={'picture_sha256':sha(picture),'audio_source_sha256':sha(original)}
    pv=next(s for s in probe(picture) if s['codec_type']=='video')
    ov=next(s for s in probe(original) if s['codec_type']=='video')
    if abs(float(pv['duration'])-float(ov['duration']))>0.001:raise ValueError('源片画面时长不一致，不能直接恢复原声')
    expected_video=stream_hash(picture,'0:v:0');expected_audio=stream_hash(original,'0:a:0')
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if any(old.get(k)!=v for k,v in identity.items()) or not output.is_file() or old.get('output_sha256')!=sha(output):raise ValueError('原声恢复记录或产物已改变')
        return old
    output.parent.mkdir(parents=True,exist_ok=True)
    if not output.exists():
        fd,tmp=tempfile.mkstemp(suffix='.mp4',dir=output.parent);os.close(fd)
        try:
            subprocess.run(['ffmpeg','-v','error','-y','-i',str(picture),'-i',str(original),'-map','0:v:0','-map','1:a:0','-c','copy','-movflags','+faststart',tmp],check=True)
            os.link(tmp,output)  # no overwrite even if another executor wins
        finally:Path(tmp).unlink(missing_ok=True)
    video_ok=stream_hash(output,'0:v:0')==expected_video;audio_ok=stream_hash(output,'0:a:0')==expected_audio
    if not video_ok or not audio_ok:raise ValueError('输出画面或原声音频包不匹配')
    result={**identity,'output_sha256':sha(output),'picture_packets_identical':video_ok,'original_audio_packets_identical':audio_ok,'output':str(output),'method':'stream_copy_no_reencoding','review':'pending'}
    receipt.parent.mkdir(parents=True,exist_ok=True)
    with receipt.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--picture',required=True);p.add_argument('--original',required=True);p.add_argument('--output',required=True);p.add_argument('--receipt',required=True)
    a=p.parse_args();print(json.dumps(preserve(a.picture,a.original,a.output,a.receipt),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
