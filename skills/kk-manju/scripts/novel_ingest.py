#!/usr/bin/env python3
"""Ingest a user-provided novel TXT/ZIP without executing or silently reordering it."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile

HEADING = re.compile(r"(?m)^第\s*(\d+)\s*章[^\r\n]*")
MAX_BYTES = 32 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_source(path: Path) -> tuple[bytes, dict]:
    original = path.read_bytes()
    if len(original) > MAX_BYTES:
        raise ValueError("source archive is too large")
    meta = {"source_file": str(path), "source_sha256": sha256(original)}
    if path.suffix.lower() != ".zip":
        return original, meta
    try:
        with ZipFile(path) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            if len(members) != 1 or not members[0].filename.lower().endswith(".txt"):
                raise ValueError("ZIP must contain exactly one TXT file")
            item = members[0]
            if item.file_size > MAX_BYTES or item.flag_bits & 1:
                raise ValueError("TXT is too large or encrypted")
            raw = archive.read(item)
            if len(raw) != item.file_size:
                raise ValueError("TXT size mismatch")
            meta["zip_member"] = item.filename
            return raw, meta
    except BadZipFile as exc:
        raise ValueError("invalid ZIP archive") from exc


def decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("TXT is neither UTF-8 nor GB18030")


def index_text(text: str) -> dict:
    matches = list(HEADING.finditer(text))
    chapters = []
    bodies = []
    for i, match in enumerate(matches):
        number = int(match.group(1))
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.start():body_end]
        clean_body = re.sub(r"\s+", "", text[match.end():body_end])
        bodies.append(clean_body)
        chapters.append({"number": number, "title": match.group().strip(),
                         "start_char": match.start(), "end_char": body_end,
                         "sha256_utf8": sha256(body.encode("utf-8")),
                         "body_sha256_utf8": sha256(clean_body.encode("utf-8")),
                         "body_chars": len(clean_body)})
    numbers = [c["number"] for c in chapters]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    jumps = [{"from": numbers[i - 1], "to": numbers[i]}
             for i in range(1, len(numbers)) if numbers[i] != numbers[i - 1] + 1]
    by_body = {}
    for chapter in chapters:
        if chapter["body_chars"] >= 100:
            by_body.setdefault(chapter["body_sha256_utf8"], []).append(chapter["number"])
    exact_body_duplicates = [group for group in by_body.values() if len(group) > 1]
    def shingles(body: str) -> set[str]:
        return {body[i:i + 8] for i in range(max(0, len(body) - 7))}
    near_adjacent = []
    previous = set()
    for i, body in enumerate(bodies):
        current = shingles(body)
        if i and len(body) >= 100 and len(bodies[i - 1]) >= 100:
            union = len(current | previous)
            similarity = len(current & previous) / union if union else 0
            if similarity >= 0.65:
                near_adjacent.append({"first_index": i - 1, "second_index": i,
                                      "first_number": chapters[i - 1]["number"],
                                      "second_number": chapters[i]["number"],
                                      "jaccard_8gram": round(similarity, 3)})
        previous = current
    short_chapters = [{"index": i, "number": c["number"], "body_chars": c["body_chars"]}
                      for i, c in enumerate(chapters) if c["body_chars"] < 100]
    return {"chapter_count": len(chapters), "duplicate_numbers": duplicates,
            "sequence_jumps": jumps, "exact_body_duplicate_groups": exact_body_duplicates,
            "near_duplicate_adjacent": near_adjacent, "short_chapters": short_chapters,
            "chapters": chapters}


def ingest(source: Path, output_dir: Path) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    raw, meta = load_source(source)
    decoded, encoding = decode(raw)
    text = decoded.replace("\r\n", "\n").replace("\r", "\n")
    index = index_text(text)
    if not index["chapters"]:
        raise ValueError("no chapter headings found")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "原始文本字节.txt").write_bytes(raw)
    (output_dir / "阅读版-UTF8.txt").write_text(text, encoding="utf-8")
    index.update(meta)
    index["source_encoding"] = encoding
    index["raw_txt_sha256"] = sha256(raw)
    index["normalized_utf8_sha256"] = sha256(text.encode("utf-8"))
    (output_dir / "章节索引.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    result = ingest(args.source, args.output)
    print(json.dumps({k: result[k] for k in ("chapter_count", "duplicate_numbers",
              "sequence_jumps", "source_encoding", "raw_txt_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
