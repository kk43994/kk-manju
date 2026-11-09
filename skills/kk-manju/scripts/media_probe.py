#!/usr/bin/env python3
"""Probe local media and optionally extract inspection frames with ffmpeg."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

def probe(source):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ValueError("input must be an existing local media file")
    binary = shutil.which("ffprobe")
    if not binary:
        raise ValueError("ffprobe is unavailable")
    result = subprocess.run([binary, "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(source)], capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    return {"source": str(source), "bytes": source.stat().st_size, "duration_seconds": duration,
            "video": {k: video.get(k) for k in ("width", "height", "codec_name", "r_frame_rate")} if video else None,
            "audio": [{k: s.get(k) for k in ("codec_name", "sample_rate", "channels")} for s in audio]}

def sample(info, dest, times=None):
    if not info["video"] or info["duration_seconds"] <= 0:
        raise ValueError("sampling requires a video with known positive duration")
    binary = shutil.which("ffmpeg")
    if not binary:
        raise ValueError("ffmpeg is unavailable")
    duration = info["duration_seconds"]
    selected = times if times is not None else [0.0, duration / 2, max(0.0, duration - 0.15)]
    selected = sorted(set(selected))
    if not selected or any(not math.isfinite(t) or not 0 <= t < duration for t in selected):
        raise ValueError("sample times must be finite and within the video duration")
    dest = Path(dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("output folder is not empty; choose a new review folder")
    dest.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(selected):
        path = dest / ("frame-%03d-%09.3fs.png" % (i, t))
        subprocess.run([binary, "-v", "error", "-n", "-ss", str(t), "-i", info["source"],
                        "-frames:v", "1", "-threads", "1", str(path)],
                       capture_output=True, text=True, check=True)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("ffmpeg produced no frame at " + str(t))
        frames.append({"requested_time_seconds": t, "path": str(path),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    record = dict(info, frames=frames,
                  review_note="Actual extracted frames; not automatic acceptance or future reference bindings.")
    (dest / "media-review.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    return record

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input")
    p.add_argument("--frames", help="new or empty output folder")
    p.add_argument("--times", help="comma-separated seconds; requires --frames")
    args = p.parse_args()
    try:
        if args.times and not args.frames:
            raise ValueError("--times requires --frames")
        info = probe(args.input)
        if args.frames:
            times = [float(t) for t in args.times.split(",")] if args.times else None
            info = sample(info, args.frames, times)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(json.dumps({"error": detail}, ensure_ascii=False))
        return 2

if __name__ == "__main__":
    sys.exit(main())
