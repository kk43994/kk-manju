#!/usr/bin/env python3
"""Local ASR evidence for generated footage; never approves wording or voice quality."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import numpy as np
from faster_whisper import WhisperModel

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory', type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--model', default='small')
    ap.add_argument('--language', default='zh',
                    help='project language code, e.g. en/zh/hi; auto enables detection')
    ap.add_argument('--model-cache', default=str(Path.home() / '.cache' / 'kk-manju-asr'))
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pending = []
    for path in sorted(args.directory.rglob('*.mp4')):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out = args.output / (path.stem + '-asr.json')
        if out.exists():
            cached = json.loads(out.read_text())
            if (cached.get('source_sha256') == digest
                    and cached.get('model') == args.model
                    and cached.get('language_requested', 'zh') == args.language):
                continue
        pending.append((path, digest, out))
    if not pending:
        print('No new media to transcribe', flush=True)
        return
    model = WhisperModel(args.model, device='cpu', compute_type='int8', cpu_threads=3,
                         download_root=args.model_cache, local_files_only=True)
    for path, digest, out in pending:
        raw = subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-vn',
                                       '-ar','16000','-ac','1','-f','s16le','-'])
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
        segments, info = model.transcribe(audio,
                                         language=None if args.language == 'auto' else args.language,
                                         beam_size=4,
                                         word_timestamps=True, vad_filter=True)
        rows = [{'start':s.start,'end':s.end,'text':s.text,
                 'words':[{'start':w.start,'end':w.end,'word':w.word} for w in (s.words or [])]}
                for s in segments]
        result = {'source':str(path.resolve()),'source_sha256':digest,'model':args.model,
                  'language_requested':args.language,'language_detected':info.language,
                  'boundary':'ASR is a locating aid; not exact script, speaker, timbre or listening approval.',
                  'segments':rows}
        temp = out.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        temp.replace(out)
        print(json.dumps({'file':path.name,'segments':[{k:r[k] for k in ('start','end','text')} for r in rows]},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    main()
