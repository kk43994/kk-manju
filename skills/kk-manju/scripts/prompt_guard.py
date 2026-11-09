#!/usr/bin/env python3
"""Prompt length preflight; local only. Never truncates or submits."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

# Operational budgets, NOT verified provider hard limits.
WORKING_BUDGET_CHARS = {"mj-v8.1": 900, "mj-v8.2": 900}
FIELDS = {"max_chars": "characters", "max_utf16_units": "utf16_units",
          "max_utf8_bytes": "utf8_bytes", "max_tokens": "tokens"}

def positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

def check(text, model="", limits=None):
    if not isinstance(text, str):
        return {"ok": False, "errors": ["prompt must be text"], "warnings": []}
    counts = {"characters": len(text),
              "utf16_units": len(text.encode("utf-16-le")) // 2,
              "utf8_bytes": len(text.encode("utf-8")),
              "words_informational_only": len(re.findall(r"\b[\w-]+\b", text)),
              "tokens": None}
    errors, warnings, constraints = [], [], []
    if limits is None:
        limits = {}
    if not isinstance(limits, dict):
        errors.append("prompt_limits must be an object")
        limits = {}
    extra = set(limits) - set(FIELDS) - {"source", "token_count"}
    if extra:
        errors.append("unknown prompt_limits fields: " + ", ".join(sorted(extra)))
    budget = WORKING_BUDGET_CHARS.get(model)
    if budget:
        constraints.append({"kind": "working_budget", "unit": "characters",
                            "maximum": budget, "source": "KK operational budget; not provider limit"})
    declared = False
    for name, unit in FIELDS.items():
        if name not in limits:
            continue
        declared = True
        maximum = limits[name]
        if not positive_int(maximum):
            errors.append(name + " must be a positive integer")
            continue
        if not isinstance(limits.get("source"), str) or not limits["source"].strip():
            errors.append(name + " requires a current source")
        constraints.append({"kind": "declared_limit", "unit": unit,
                            "maximum": maximum, "source": limits.get("source")})
    if "token_count" in limits:
        value = limits["token_count"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append("token_count must be a non-negative exact count")
        else:
            counts["tokens"] = value
    for constraint in constraints:
        unit = constraint["unit"]
        used = counts[unit]
        if used is None:
            errors.append("token limit requires an exact count from the correct tokenizer; words are not tokens")
        elif used > constraint["maximum"]:
            errors.append(f"{constraint['kind']} exceeded: {used} {unit} > {constraint['maximum']}")
    if not declared:
        warnings.append("provider hard limit unverified; working budget is not a hard-limit guarantee")
    return {"ok": not errors, "model": model, "counts": counts,
            "constraints": constraints, "errors": errors, "warnings": warnings,
            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt_file")
    parser.add_argument("--model", required=True)
    parser.add_argument("--limits-json", help="JSON file containing limits and their source")
    args = parser.parse_args()
    text = Path(args.prompt_file).read_text(encoding="utf-8")
    limits = json.loads(Path(args.limits_json).read_text(encoding="utf-8")) if args.limits_json else None
    result = check(text, args.model, limits)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
