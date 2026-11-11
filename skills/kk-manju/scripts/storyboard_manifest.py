#!/usr/bin/env python3
"""Extract auditable shot groups and sound mentions from a director storyboard.

This is an inventory, not a video-job generator: timings, references and
speaker assignments still need explicit production decisions.
"""

import argparse
import json
import re
from pathlib import Path

SHOT = re.compile(r"^###\s+(S\d{2})(?:-([A-Z]))?｜(.+)$", re.M)
EVENT = re.compile(r"\bA\d{2}\b")
VOICE = re.compile(r"^[-*]\s+\*\*(A\d{2})\s+([^*]+)\*\*：(.*)$", re.M)


def build(source: str) -> dict:
    main, _, voice_section = source.partition("## 原文声音事件")
    matches = list(SHOT.finditer(main))
    shots = []
    for i, match in enumerate(matches):
        body = main[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(main)]
        body = body.split("\n## ", 1)[0].strip()
        shots.append({
            "shot": match.group(1),
            "phase": match.group(2),
            "title": match.group(3).strip(),
            "director_text": body,
            "mentioned_sound_events": sorted(set(EVENT.findall(body))),
        })
    groups = []
    for shot in shots:
        if not groups or groups[-1]["shot"] != shot["shot"]:
            groups.append({"shot": shot["shot"], "phases": [], "mentioned_sound_events": []})
        groups[-1]["phases"].append(shot)
        groups[-1]["mentioned_sound_events"] = sorted(set(groups[-1]["mentioned_sound_events"] + shot["mentioned_sound_events"]))
    voices = {}
    for event_id, role, line in VOICE.findall(voice_section):
        if event_id in voices:
            raise ValueError(f"duplicate sound event {event_id}")
        voices[event_id] = {"role": role.strip(), "text": line.strip()}
    missing = sorted(set(e for g in groups for e in g["mentioned_sound_events"]) - set(voices))
    if missing:
        raise ValueError(f"storyboard references missing sound events: {missing}")
    return {"shot_count": len(groups), "phase_count": len(shots), "sound_event_count": len(voices), "shots": groups, "sound_events": voices}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("storyboard", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    manifest = build(args.storyboard.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("shot_count", "phase_count", "sound_event_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
