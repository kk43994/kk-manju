#!/usr/bin/env python3
"""Non-destructive Bandit-v2/ELUATE music-removal trial on one source range.

Requires eluate==0.0.3 and a separately downloaded official checkpoint.
Outputs are candidates. Speech/effects preservation requires listening.
"""
import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import time
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(argv):
    subprocess.run(argv, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--duration', type=float, required=True)
    p.add_argument('--device', choices=['cpu','mps','cuda'], default='cpu')
    args = p.parse_args()
    if args.start < 0 or not 0 < args.duration <= 30:
        p.error('Trial range must be positive, at most 30 seconds')
    if args.out.exists() and any(args.out.iterdir()):
        raise FileExistsError('Use a fresh trial directory; do not overwrite previous candidates')
    args.out.mkdir(parents=True, exist_ok=True)
    checkpoint_sha = sha(args.checkpoint)
    expected = 'abcfccf65446752a057f4a302c941479a54b7560ebf8d7bca039d2ea98e64cfc'
    if checkpoint_sha != expected:
        raise ValueError('Expected verified official multilingual Bandit v2 checkpoint')
    manifest = {'source': str(args.source.resolve()), 'source_sha256': sha(args.source),
                'source_start_s': args.start, 'requested_duration_s': args.duration,
                'checkpoint_sha256': checkpoint_sha,
                'model_source': 'https://zenodo.org/records/12701995',
                'weights_license': 'CC-BY-SA-4.0', 'model': 'Bandit v2 multilingual',
                'adapter': 'ELUATE 0.0.3', 'status': 'running', 'device': args.device,
                'quality_review': 'not_completed', 'adopted': False}
    receipt = args.out/'trial.json'
    receipt.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    start = time.monotonic()
    try:
        import numpy as np
        import soundfile as sf
        import torch
        import yaml
        from eluate.core.separator import BanditSeparator
        from eluate.utils.paths import get_config_path
        torch.set_num_threads(4)
        cfg = yaml.safe_load(get_config_path().read_text())
        cfg['inference']['batch_size'] = 1
        config = args.out/'config.yaml'
        config.write_text(yaml.safe_dump(cfg))
        original = args.out/'original.wav'
        run(['ffmpeg','-v','error','-n','-ss',str(args.start),'-i',str(args.source),
             '-t',str(args.duration),'-vn','-ar','48000','-ac','2','-c:a','pcm_f32le',str(original)])
        sep = BanditSeparator(config_path=config, checkpoint_path=args.checkpoint,
                              device=torch.device(args.device))
        last = [-1]
        def progress(x):
            if int(x*10) != last[0]:
                last[0] = int(x*10)
                print(json.dumps({'progress':round(x,3),'elapsed_s':round(time.monotonic()-start,1)}),flush=True)
        sep.produce_outputs(original, workspace_dir=args.out/'temporary',
                            speech_path=args.out/'speech.wav', sfx_path=args.out/'effects.wav',
                            progress_callback=progress)
        sep.close()
        a, sr = sf.read(original, always_2d=True)
        speech, ssr = sf.read(args.out/'speech.wav', always_2d=True)
        effects, esr = sf.read(args.out/'effects.wav', always_2d=True)
        if a.shape != speech.shape or a.shape != effects.shape or sr != ssr or sr != esr:
            raise ValueError('Output lengths/sample rates differ; do not silently truncate')
        if not all(np.isfinite(x).all() for x in [a, speech, effects]):
            raise ValueError('Nonfinite samples; reject candidate')
        cleaned = speech + effects
        peak = float(np.max(np.abs(cleaned)))
        gain = min(1.0, 0.98/max(peak,1e-12))
        sf.write(args.out/'speech-plus-effects.wav', cleaned*gain, sr, subtype='FLOAT')
        # This is not the model music stem: residual also contains reconstruction error.
        sf.write(args.out/'removed-residual.wav', a-cleaned, sr, subtype='FLOAT')
        run(['ffmpeg','-v','error','-n','-ss',str(args.start),'-i',str(args.source),
             '-i',str(args.out/'speech-plus-effects.wav'),'-t',str(len(a)/sr),
             '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','fast','-crf','19',
             '-c:a','aac','-b:a','192k','-movflags','+faststart',str(args.out/'no-music-candidate.mp4')])
        manifest.update(status='completed_candidate', elapsed_s=round(time.monotonic()-start,2),
                        samples=len(a), sample_rate=sr, channels=a.shape[1], output_gain=gain,
                        metrics={'original_rms':float(np.sqrt(np.mean(a*a))),
                                 'speech_rms':float(np.sqrt(np.mean(speech*speech))),
                                 'effects_rms':float(np.sqrt(np.mean(effects*effects))),
                                 'removed_residual_rms':float(np.sqrt(np.mean((a-cleaned)**2)))},
                        versions={x:importlib.metadata.version(x) for x in ['eluate','torch','numpy','soundfile']},
                        limitation='Numerical checks do not establish music removal or speech/effects fidelity; listen to A/B and residual.')
    except Exception as exc:
        manifest.update(status='failed', error=repr(exc), elapsed_s=round(time.monotonic()-start,2))
        raise
    finally:
        receipt.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
