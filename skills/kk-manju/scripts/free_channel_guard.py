#!/usr/bin/env python3
"""Verify a fresh, exact zero-credit Web quote before dispatching a claimed job.

This script neither queries LibTV nor submits generation. The operator records
the visible quote and a screenshot; the guard binds that evidence to the frozen
pipeline request, so a different model, duration, count, prompt or reference
set cannot silently reuse it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys


def fingerprint(request: dict) -> str:
    payload = {
        "request_key": request.get("request_key"),
        "prompt": request.get("prompt"),
        "parameters": request.get("parameters"),
        "references": request.get("references"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify(request: dict, quote: dict, proof: Path, now: datetime | None = None,
           expected_resolution: str = "480p") -> list[str]:
    errors = []
    params = request.get("parameters") or {}
    if request.get("kind") != "video":
        errors.append("request is not a video job")
    if params.get("model") != "star-video2-fast":
        errors.append("model is not Seedance 2.0 Fast")
    if expected_resolution not in ("480p", "720p"):
        errors.append("unsupported expected resolution")
    if params.get("resolution") != expected_resolution:
        errors.append("resolution does not match the project selection")
    # A newly selected 720p request needs explicit resolution evidence from its
    # own current Web quote. Keep legacy 480p receipts backwards compatible.
    if expected_resolution == "720p" or "resolution" in quote:
        if quote.get("resolution") != expected_resolution:
            errors.append("quoted resolution does not match the project selection")
    if type(params.get("count")) is not int or params["count"] != 1:
        errors.append("zero-credit VIP requires exactly one output per shot")
    if quote.get("project_id") != request.get("project_id"):
        errors.append("project identity mismatch")
    if quote.get("request_key") != request.get("request_key"):
        errors.append("request key mismatch")
    if quote.get("request_fingerprint") != fingerprint(request):
        errors.append("frozen request fingerprint mismatch")
    if type(quote.get("credits_total")) is not int or quote["credits_total"] != 0:
        errors.append("quote is not exactly zero credits")
    if quote.get("channel_label") != "Seedance 2.0 Fast VIP":
        errors.append("VIP channel label missing")
    if not isinstance(quote.get("account_id"), int) or quote["account_id"] <= 0:
        errors.append("account identity missing")
    try:
        quoted_at = datetime.fromisoformat(quote["quoted_at_utc"].replace("Z", "+00:00"))
        if quoted_at.utcoffset() != timedelta(0):
            errors.append("quote timestamp must be UTC")
        else:
            age = (now or datetime.now(timezone.utc)) - quoted_at
            if age < timedelta(seconds=-30) or age > timedelta(minutes=10):
                errors.append("quote is stale or from the future")
    except (KeyError, TypeError, ValueError):
        errors.append("quote timestamp is invalid")
    if not proof.is_file() or proof.stat().st_size == 0:
        errors.append("quote screenshot is missing")
    elif quote.get("proof_sha256") != hashlib.sha256(proof.read_bytes()).hexdigest():
        errors.append("quote screenshot fingerprint mismatch")
    return errors


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("request", type=Path, help="pipeline.py claim 的封存 request_file")
    p.add_argument("quote", type=Path, help="当前 Web 0 积分报价证据 JSON")
    p.add_argument("--proof", required=True, type=Path, help="显示 VIP、0 积分和参数的截图")
    p.add_argument("--project-id", required=True)
    p.add_argument("--expected-resolution", choices=("480p", "720p"), default="480p",
                   help="explicit project choice; 720p also requires quote.resolution=720p")
    args = p.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        quote = json.loads(args.quote.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"quote evidence unreadable: {exc}", file=sys.stderr)
        return 2
    request["project_id"] = args.project_id
    errors = verify(request, quote, args.proof,
                    expected_resolution=args.expected_resolution)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(json.dumps({"status": "zero_credit_quote_verified",
                      "request_key": request["request_key"],
                      "request_fingerprint": fingerprint(request)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
