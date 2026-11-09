#!/usr/bin/env python3
"""Save a prepared LibTV delivery manifest atomically; never generates media."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request


def safe_name(value):
    if (not isinstance(value, str) or not value.strip() or value in {".", ".."}
            or "/" in value or "\\" in value or "\x00" in value):
        raise ValueError("delivery filename must be a plain basename")
    return value


def checked_manifest(manifest):
    if manifest.get("status") != "ready" or manifest.get("delivery") != "awaiting_host_save":
        raise ValueError("delivery is not ready for host save")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise ValueError("delivery needs 1..100 explicit files")
    names = []
    for item in files:
        names.append(safe_name(item.get("filename")))
        if item.get("url"):
            u = urllib.parse.urlsplit(item["url"])
            if u.scheme != "https" or not u.hostname or u.username or u.password:
                raise ValueError("delivery URL must be credential-free HTTPS")
        elif not isinstance(item.get("inlineText"), str):
            raise ValueError("file needs a prepared URL or inlineText")
    if len(names) != len(set(names)):
        raise ValueError("duplicate filenames; request unique names from delivery tool")
    if manifest.get("fileCount", len(files)) != len(files):
        raise ValueError("manifest fileCount mismatch")
    return files


def verify_file(path, item):
    raw = path.read_bytes()
    if not raw:
        raise ValueError("empty delivery file")
    if item.get("sizeBytes") is not None and len(raw) != item["sizeBytes"]:
        raise ValueError("delivery byte size mismatch")
    sha = hashlib.sha256(raw).hexdigest()
    expected = item.get("contentHash")
    if expected:
        # sourceIdentity identifies the source; it is deliberately NOT a byte hash.
        expected = expected.removeprefix("sha256:").removeprefix("sha256-")
        if sha != expected:
            raise ValueError("delivery content hash mismatch")
    details = {"bytes": len(raw), "sha256": sha}
    if str(item.get("mime", "")).startswith("image/"):
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            details.update(size=list(im.size), mode=im.mode)
    return details


def save(manifest, output, workers=4):
    files = checked_manifest(manifest)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "delivery-manifest.json"
    result_path = output / "delivery-local.json"
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if manifest_path.exists() and manifest_path.read_text() != encoded:
        raise ValueError("output belongs to another manifest; use a new revision directory")
    if not manifest_path.exists():
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    previous = json.loads(result_path.read_text()) if result_path.exists() else {"files": []}
    known = {row["filename"]: row for row in previous["files"]}

    def fetch(item):
        dest = output / item["filename"]
        if dest.is_symlink():
            raise ValueError("refuse symlink destination")
        if dest.exists():
            old = known.get(item["filename"])
            if not old:
                raise ValueError("unrecorded existing file; preserve it and use a new output directory")
            measured = verify_file(dest, item)
            if measured["sha256"] != old["sha256"]:
                raise ValueError("saved file changed after delivery")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".delivery-", suffix=".part", dir=output)
            tmp = Path(temporary)
            try:
                with os.fdopen(fd, "wb") as stream:
                    if item.get("url"):
                        with urllib.request.urlopen(item["url"], timeout=60) as response:
                            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                                stream.write(chunk)
                    else:
                        stream.write(item["inlineText"].encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                measured = verify_file(tmp, item)
                # Link creation cannot overwrite an existing destination.
                os.link(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        return {"filename": item["filename"], "path": str(dest),
                "nodeId": item.get("nodeId"), "resourceId": item.get("resourceId"),
                "sourceIdentity": item.get("sourceIdentity"), **measured}

    outcomes, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, item): item for item in files}
        for future in concurrent.futures.as_completed(futures):
            try:
                outcomes.append(future.result())
            except Exception as exc:
                errors.append({"filename": futures[future]["filename"], "error": str(exc)})
    order = {item["filename"]: index for index, item in enumerate(files)}
    outcomes.sort(key=lambda row: order[row["filename"]])
    report = {"ok": not errors, "files": outcomes, "errors": errors,
              "boundary": "Saved bytes and image decode only; no artistic approval or chat delivery claim."}
    fd, temporary = tempfile.mkstemp(prefix=".delivery-report-", dir=output)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, result_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    try:
        if not 1 <= args.workers <= 16:
            raise ValueError("workers must be 1..16")
        result = save(json.loads(Path(args.manifest).read_text(encoding="utf-8-sig")),
                      args.output, args.workers)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
