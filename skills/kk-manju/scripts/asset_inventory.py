#!/usr/bin/env python3
"""Inventory explicitly supplied asset roots. Never selects or uploads assets."""
import argparse
from collections import defaultdict
import hashlib
import json
import mimetypes
from pathlib import Path
import sys

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp",
              ".mp4", ".mov", ".mkv", ".webm", ".avi", ".wav", ".mp3", ".m4a",
              ".aac", ".flac", ".ogg"}

def scan(roots):
    assets, seen, errors = [], set(), []
    for raw in roots:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("asset root is not a directory: " + str(root))
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in EXTENSIONS or not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            if not resolved.is_relative_to(root):
                errors.append({"path": str(path), "reason": "symlink escapes selected root"})
                continue
            seen.add(resolved)
            try:
                h = hashlib.sha256()
                with resolved.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                info = resolved.stat()
                assets.append({"path": str(resolved), "source_root": str(root),
                               "bytes": info.st_size, "modified_ns": info.st_mtime_ns,
                               "sha256": h.hexdigest(), "media_type": mimetypes.guess_type(path.name)[0],
                               "review_status": "unreviewed", "selection_status": "unselected"})
            except OSError as exc:
                errors.append({"path": str(resolved), "reason": str(exc)})
    grouped = defaultdict(list)
    for a in assets:
        grouped[a["sha256"]].append(a["path"])
    return {"assets": assets, "duplicates": [paths for paths in grouped.values() if len(paths) > 1],
            "errors": errors, "note": "File discovery and equality do not prove identity or visual quality."}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("roots", nargs="+")
    p.add_argument("--output")
    args = p.parse_args()
    try:
        result = scan(args.roots)
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            dest = Path(args.output).expanduser().resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("x", encoding="utf-8") as f:
                f.write(text)
            print(json.dumps({"output": str(dest), "assets": len(result["assets"]),
                              "duplicates": len(result["duplicates"])}, ensure_ascii=False))
        else:
            print(text, end="")
        return 0 if not result["errors"] else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

if __name__ == "__main__":
    sys.exit(main())
