#!/usr/bin/env python3
"""Read-only export checks: real decode, frame timing, optional delivery byte match."""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def rate(value):
    parsed = Fraction(str(value))
    if parsed <= 0:
        raise ValueError("fps must be positive")
    return float(parsed)


def verify(source, *, expected_frames=None, expected_fps=None,
           expected_duration=None, match_file=None, threads=2):
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ValueError("input must be an existing local video")
    if not isinstance(threads, int) or not 1 <= threads <= 8:
        raise ValueError("threads must be between 1 and 8")
    if expected_frames is not None and expected_frames <= 0:
        raise ValueError("expected_frames must be positive")
    fps = rate(expected_fps) if expected_fps is not None else None
    if expected_duration is not None and (
        number(expected_duration) is None or expected_duration <= 0
    ):
        raise ValueError("expected_duration must be finite and positive")
    reference = Path(match_file).expanduser().resolve() if match_file else None
    if reference is not None and not reference.is_file():
        raise ValueError("match_file must be an existing local file")
    ffprobe, ffmpeg = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise ValueError("ffprobe and ffmpeg are required")
    initial = source.stat()
    meta = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    )
    metadata = json.loads(meta.stdout)
    videos = [s for s in metadata.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise ValueError("no video stream found")
    video = videos[0]
    issues = []
    if meta.stderr.strip():
        issues.append("metadata_read_error")

    # show_frames enumerates decoded pictures, not packets or a declared nb_frames.
    observed = subprocess.run(
        [ffprobe, "-v", "error", "-threads", str(threads), "-select_streams", "v:0",
         "-show_frames", "-show_entries",
         "frame=best_effort_timestamp_time,duration_time,pkt_duration_time",
         "-of", "json", str(source)], capture_output=True, text=True,
    )
    try:
        frames = json.loads(observed.stdout).get("frames", [])
    except json.JSONDecodeError:
        frames = []
        issues.append("decoded_frame_list_unreadable")
    if observed.returncode != 0 or observed.stderr.strip():
        issues.append("frame_decode_error")
    if not frames:
        issues.append("no_decoded_frames")
    count = len(frames)
    if expected_frames is not None and count != expected_frames:
        issues.append("decoded_frame_count_mismatch")
    declared = video.get("nb_frames")
    if declared is not None and str(declared).isdigit() and int(declared) != count:
        issues.append("declared_vs_decoded_frame_count_mismatch")

    timestamps = [number(f.get("best_effort_timestamp_time")) for f in frames]
    missing = sum(t is None for t in timestamps)
    nonmonotonic, deviations = [], []
    step = 1 / fps if fps else None
    try:
        timebase = float(Fraction(video.get("time_base", "0")))
    except (ValueError, ZeroDivisionError):
        timebase = 0.0
    # Millisecond Matroska timestamps alternate 41/42 ms for legitimate 24 fps.
    step_tolerance = max(0.00001, (step or 0) * 0.01, timebase * 1.1)
    for index in range(1, count):
        previous, current = timestamps[index - 1:index + 1]
        if previous is None or current is None:
            continue
        delta = current - previous
        if delta <= 0:
            nonmonotonic.append({"frame": index, "delta_seconds": delta})
        if step and abs(delta - step) > step_tolerance:
            deviations.append({"frame": index, "pts_seconds": current,
                               "delta_seconds": delta, "expected_seconds": step})
    if missing:
        issues.append("missing_frame_timestamps")
    if nonmonotonic:
        issues.append("nonmonotonic_frame_timestamps")
    if deviations:
        issues.append("expected_cfr_step_mismatch")

    last_duration = None
    duration_basis = "last decoded frame duration"
    if frames:
        for key in ("duration_time", "pkt_duration_time"):
            candidate = number(frames[-1].get(key))
            if candidate is not None and candidate > 0:
                last_duration = candidate
                break
    if last_duration is None and step:
        last_duration = step
        duration_basis = "last frame duration inferred from explicitly expected fps"
    span = None
    if timestamps and not missing and last_duration is not None:
        span = timestamps[-1] + last_duration - timestamps[0]
    if expected_duration is not None:
        tolerance = step / 2 if step else 0.001
        if span is None:
            issues.append("decoded_duration_unverified")
        elif abs(span - expected_duration) > tolerance:
            issues.append("decoded_duration_mismatch")

    # Decode video and all audio. Do not add fps/padding filters that hide missing pictures.
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
         "-threads", str(threads), "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
         "-fps_mode", "passthrough", "-threads", str(threads), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    decode_passed = decoded.returncode == 0 and not decoded.stderr.strip()
    if not decode_passed:
        issues.append("full_audio_video_decode_failed")
    identity = fingerprint(source)
    compared = None
    if reference is not None:
        compared = {"source": str(reference), **fingerprint(reference)}
        compared["byte_identical"] = all(compared[k] == identity[k] for k in ("bytes", "sha256"))
        if not compared["byte_identical"]:
            issues.append("delivery_byte_mismatch")
    final_stat = source.stat()
    if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (
        final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ino
    ):
        issues.append("source_changed_during_verification")

    return {
        "source": str(source), **identity,
        "technical_status": "failed" if issues else "passed",
        "issues": issues,
        "expected": {"frames": expected_frames, "fps": expected_fps,
                     "video_duration_seconds": expected_duration},
        "video": {k: video.get(k) for k in (
            "width", "height", "codec_name", "pix_fmt", "r_frame_rate", "avg_frame_rate",
            "time_base", "start_time", "duration", "nb_frames")},
        "audio": [{k: s.get(k) for k in (
            "codec_name", "sample_rate", "channels", "start_time", "duration")}
                  for s in metadata.get("streams", []) if s.get("codec_type") == "audio"],
        "decoded_frames": count,
        "first_video_pts": timestamps[0] if timestamps else None,
        "last_video_pts": timestamps[-1] if timestamps else None,
        "decoded_video_span_seconds": span,
        "duration_basis": duration_basis if span is not None else "unknown",
        "missing_pts_count": missing,
        "nonmonotonic_pts_count": len(nonmonotonic), "nonmonotonic_pts_sample": nonmonotonic[:20],
        "cfr_check": "checked" if fps else "not requested; VFR intervals not classified as dropped frames",
        "cfr_step_tolerance_seconds": step_tolerance if fps else None,
        "cfr_deviation_count": len(deviations), "cfr_deviation_sample": deviations[:20],
        "full_audio_video_decode_passed": decode_passed,
        "decoder_error_tail": (meta.stderr + observed.stderr + decoded.stderr)[-5000:],
        "delivery_comparison": compared,
        "review_boundary": "Technical checks of first video/all audio only; does not detect all repeated "
                           "images, verify subtitles, lip sync, voice identity or artistic continuity. "
                           "Playback/listening and project-specific visual review remain necessary.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--expected-frames", type=int)
    parser.add_argument("--expected-fps", help="only for expected CFR, e.g. 24 or 24000/1001")
    parser.add_argument("--expected-duration", type=float, help="video duration from edit, in seconds")
    parser.add_argument("--match-file", help="compare local download against reviewed original bytes")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--report", help="new JSON file; never overwrite an existing report")
    args = parser.parse_args()
    try:
        report = Path(args.report).expanduser().resolve() if args.report else None
        if report is not None and report.exists():
            raise ValueError("report exists; choose a new report path")
        result = verify(args.input, expected_frames=args.expected_frames,
                        expected_fps=args.expected_fps, expected_duration=args.expected_duration,
                        match_file=args.match_file, threads=args.threads)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if report is not None:
            report.parent.mkdir(parents=True, exist_ok=True)
            with report.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        print(rendered, end="")
        return 0 if result["technical_status"] == "passed" else 1
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(json.dumps({"error": detail}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
