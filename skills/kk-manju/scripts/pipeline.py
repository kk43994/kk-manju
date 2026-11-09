#!/usr/bin/env python3
"""Local production planner. No network calls or paid generation."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import prompt_guard
import production_contract

ACTIVE = {"claimed", "submitted", "running", "unknown"}
KINDS = {"image", "video", "audio", "edit", "sample"}
CHANNELS = {"chat", "canvas"}
ALLOWED_TRANSITIONS = {
    "claimed": {"submitted", "running", "succeeded", "failed", "unknown"},
    "submitted": {"submitted", "running", "succeeded", "failed", "unknown"},
    "running": {"running", "succeeded", "failed", "unknown"},
    "unknown": {"submitted", "running", "succeeded", "failed", "unknown"},
    "succeeded": {"succeeded"}, "failed": {"failed"},
}

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def resolve(root, value):
    p = Path(value).expanduser()
    return (p if p.is_absolute() else root / p).resolve()

def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0

def validate(plan, root):
    errors, warnings = [], []
    jobs = plan.get("jobs", [])
    if plan.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for key in ("project_id", "revision"):
        if not isinstance(plan.get(key), str) or not plan[key].strip():
            errors.append(key + " is required")
    limits = plan.get("limits", {})
    if not positive_int(limits.get("total")):
        errors.append("limits.total must be a positive integer")
    if not isinstance(limits.get("by_kind", {}), dict):
        errors.append("limits.by_kind must be an object")
    else:
        for k, v in limits.get("by_kind", {}).items():
            if k not in KINDS or not positive_int(v):
                errors.append("invalid per-kind limit: " + str(k))
    auth = plan.get("authorized_kinds", [])
    if not isinstance(auth, list) or any(k not in KINDS for k in auth):
        errors.append("authorized_kinds must be a list of known kinds")
    if not isinstance(jobs, list):
        return ["jobs must be a list"], warnings
    ids = [j.get("id") for j in jobs if isinstance(j, dict)]
    if len(ids) != len(jobs) or any(not isinstance(i, str) or not i for i in ids):
        return errors + ["every job needs a non-empty string id"], warnings
    if len(set(ids)) != len(ids):
        return errors + ["duplicate job id"], warnings
    by_id = {j["id"]: j for j in jobs}
    source = plan.get("dialogue_source", {})
    if not isinstance(source, dict):
        errors.append("dialogue_source must map source ids to exact text")
        source = {}
    assembled, shot_ids = {}, set()
    for job in jobs:
        jid = job["id"]
        kind = job.get("kind")
        if kind not in KINDS:
            errors.append(jid + ": unknown kind")
        deps = job.get("depends_on", [])
        if not isinstance(deps, list) or any(d not in by_id for d in deps):
            errors.append(jid + ": dependency missing")
            deps = []
        if jid in deps:
            errors.append(jid + ": self dependency")
        for k in ("review_required",):
            if k in job and not isinstance(job[k], bool):
                errors.append(jid + ": " + k + " must be boolean")
        if any(c not in CHANNELS for c in job.get("delivery_channels", [])):
            errors.append(jid + ": unknown delivery channel")
        prompt = job.get("prompt_file")
        if kind in {"image", "video", "audio"} and not prompt:
            errors.append(jid + ": prompt_file is required")
        if prompt and (not resolve(root, prompt).is_file()
                       or not resolve(root, prompt).stat().st_size):
            errors.append(jid + ": prompt file missing or empty")
        elif prompt:
            raw_prompt = resolve(root, prompt).read_text(encoding="utf-8")
            try:
                final_prompt = production_contract.render_prompt(plan, job, raw_prompt)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                errors.append(jid + ": invalid director contract: " + str(exc))
                final_prompt = raw_prompt
            measured = prompt_guard.check(final_prompt,
                                          job.get("parameters", {}).get("model", job.get("model", "")),
                                          job.get("prompt_limits"))
            errors.extend(jid + ": " + e for e in measured["errors"])
            warnings.extend(jid + ": " + w for w in measured["warnings"])
        for ref in job.get("references", []):
            if not ref.get("role"):
                errors.append(jid + ": reference needs a role")
            selectors = [k for k in ("path", "node_id", "url", "source_job") if ref.get(k)]
            if len(selectors) != 1:
                errors.append(jid + ": each reference needs one source selector")
            if ref.get("path") and not resolve(root, ref["path"]).is_file():
                errors.append(jid + ": reference file missing: " + ref["path"])
            if ref.get("source_job") and ref["source_job"] not in deps:
                errors.append(jid + ": reference job must be a dependency")
            if "output_index" in ref and (not isinstance(ref["output_index"], int)
                                         or isinstance(ref["output_index"], bool)
                                         or ref["output_index"] < 0):
                errors.append(jid + ": invalid output_index")
        continuity = job.get("continuity", {})
        previous = continuity.get("from_job")
        if previous:
            if previous not in by_id:
                errors.append(jid + ": continuity source missing")
            mode = continuity.get("mode", "cut")
            if mode not in {"cut", "continuation", "time_jump"}:
                errors.append(jid + ": invalid continuity mode")
            if mode == "continuation":
                if previous not in deps:
                    errors.append(jid + ": continuation must depend on prior result")
                if not any(r.get("source_job") == previous for r in job.get("references", [])):
                    errors.append(jid + ": continuation needs an actual prior-output reference")
            if mode != "time_jump" and previous in by_id:
                end = by_id[previous].get("end_state", {})
                start = job.get("start_state", {})
                changes = continuity.get("intentional_changes", {})
                for key in continuity.get("match_keys", []):
                    if key not in end or key not in start:
                        errors.append(jid + ": continuity field missing: " + key)
                    elif end[key] != start[key] and not changes.get(key):
                        errors.append(jid + ": planned continuity mismatch: " + key)
        duration = job.get("duration_seconds")
        shots = job.get("shots", [])
        if kind == "video" and (not numeric(duration) or duration <= 0):
            errors.append(jid + ": video duration_seconds must be positive")
        shot_total = 0.0
        for shot in shots:
            sid = shot.get("id")
            seconds = shot.get("duration_seconds")
            if not sid or sid in shot_ids:
                errors.append(jid + ": missing or repeated shot id")
            shot_ids.add(sid)
            if not numeric(seconds) or seconds <= 0:
                errors.append(jid + ": invalid shot duration")
                continue
            shot_total += seconds
            events = shot.get("events", [])
            events_by_id = {e.get("id"): e for e in events}
            if len(events_by_id) != len(events) or None in events_by_id:
                errors.append(str(sid) + ": event ids must be unique and non-empty")
            for event in events:
                a, b = event.get("start"), event.get("end")
                if not numeric(a) or not numeric(b) or not 0 <= a < b <= seconds:
                    errors.append(str(sid) + ": event outside shot time window")
                    continue
                for before in event.get("after", []):
                    if before not in events_by_id or events_by_id[before].get("end", math.inf) > a:
                        errors.append(str(sid) + ": sequential event overlap: " + str(before))
                if event.get("kind") == "dialogue":
                    source_id, txt = event.get("source_id"), event.get("text")
                    if not isinstance(txt, str) or not txt:
                        errors.append(str(sid) + ": dialogue text missing")
                    elif source_id not in source:
                        errors.append(str(sid) + ": dialogue source missing")
                    else:
                        assembled[source_id] = assembled.get(source_id, "") + txt
        if shots and numeric(duration):
            handles = job.get("handles_seconds", 0)
            if not numeric(handles) or handles < 0:
                errors.append(jid + ": handles_seconds invalid")
            elif not math.isclose(shot_total + handles, duration, abs_tol=0.001):
                errors.append(jid + ": shot total plus handles differs from job duration")
    for key, txt in source.items():
        if assembled.get(key, "") != txt:
            errors.append("dialogue coverage differs from source: " + str(key))
    visiting, visited = set(), set()
    def visit(jid):
        if jid in visiting:
            errors.append("dependency cycle at " + jid)
            return
        if jid in visited:
            return
        visiting.add(jid)
        for dep in by_id[jid].get("depends_on", []):
            if dep in by_id:
                visit(dep)
        visiting.remove(jid)
        visited.add(jid)
    for jid in by_id:
        visit(jid)
    if not jobs:
        warnings.append("No jobs: fill the plan before scheduling.")
    if not auth:
        warnings.append("No authorized kinds: queue will not dispatch.")
    errors.extend(production_contract.validate(plan, root))
    return errors, warnings

def snapshot(plan, root):
    files = {}
    candidates = [plan.get("source_file"), *production_contract.inputs(plan)]
    for job in plan["jobs"]:
        candidates += [job.get("prompt_file"), job.get("shot_design_file")]
        candidates += [r.get("path") for r in job.get("references", [])]
    for value in candidates:
        if value:
            p = resolve(root, value)
            if not p.is_file():
                raise ValueError("input file missing: " + str(p))
            files[str(p)] = file_hash(p)
    return digest({"plan": plan, "files": files}), files

def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def init_state(plan, signature):
    return {"schema_version": 1, "project_id": plan["project_id"],
            "revision": plan["revision"], "plan_digest": signature, "created_at": now(),
            "plan_snapshot": json.loads(json.dumps(plan)),
            "jobs": {j["id"]: {"status": "pending", "review": "pending", "outputs": [],
                                  "delivery": {}, "receipts": [],
                                  "request_key": "kk-" + digest([plan["project_id"], plan["revision"],
                                                               signature, j["id"]])[:40]}
                     for j in plan["jobs"]}, "events": []}

def event(state, jid, action, details=None):
    state["events"].append({"at": now(), "job": jid, "action": action, "details": details or {}})

def resolve_refs(job, state, root):
    refs = []
    for ref in job.get("references", []):
        item = dict(ref)
        if ref.get("source_job"):
            outputs = state["jobs"][ref["source_job"]]["outputs"]
            if not outputs:
                raise ValueError("dependency has no actual output")
            if len(outputs) > 1 and "output_index" not in ref:
                raise ValueError("multiple dependency outputs; specify output_index")
            idx = ref.get("output_index", 0)
            if idx >= len(outputs):
                raise ValueError("output_index exceeds returned outputs")
            output = outputs[idx]
            if output.get("path"):
                path = resolve(root, output["path"])
                if not path.is_file() or (output.get("sha256") and file_hash(path) != output["sha256"]):
                    raise ValueError("dependency output file missing or changed")
            item["resolved_output"] = output
        elif ref.get("path"):
            item["path"] = str(resolve(root, ref["path"]))
        refs.append(item)
    return refs

def blockers(job, plan, state, root):
    reasons = []
    jid = job["id"]
    if state["jobs"][jid]["status"] != "pending":
        return [state["jobs"][jid]["status"]]
    if job["kind"] not in plan.get("authorized_kinds", []):
        reasons.append("kind not authorized in this plan")
    by_id = {j["id"]: j for j in plan["jobs"]}
    for dep in job.get("depends_on", []):
        ds = state["jobs"][dep]
        if ds["status"] != "succeeded":
            reasons.append(dep + ": " + ds["status"])
        elif by_id[dep].get("review_required", False) and ds["review"] != "passed":
            reasons.append(dep + ": review " + ds["review"])
    if not reasons:
        try:
            resolve_refs(job, state, root)
        except ValueError as exc:
            reasons.append(str(exc))
    c = job.get("continuity", {})
    if c.get("require_actual_state") and c.get("from_job"):
        actual = state["jobs"][c["from_job"]].get("observed_end_state")
        if actual is None:
            reasons.append("observed end state not recorded")
        else:
            for key in c.get("match_keys", []):
                if key not in actual or actual[key] != job.get("start_state", {}).get(key):
                    if not c.get("intentional_changes", {}).get(key):
                        reasons.append("actual continuity mismatch: " + key)
    return reasons

def ready_jobs(plan, state, root, requested=None):
    occupied = [j for j in plan["jobs"] if state["jobs"][j["id"]]["status"] in ACTIVE]
    free = max(0, plan["limits"]["total"] - len(occupied))
    if requested is not None:
        free = min(free, requested)
    counts = Counter(j["kind"] for j in occupied)
    selected, blocked = [], {}
    for job in sorted(plan["jobs"], key=lambda j: -j.get("priority", 0)):
        reasons = blockers(job, plan, state, root)
        if reasons:
            blocked[job["id"]] = reasons
            continue
        kind_limit = plan["limits"].get("by_kind", {}).get(job["kind"], plan["limits"]["total"])
        if len(selected) >= free or counts[job["kind"]] >= kind_limit:
            blocked[job["id"]] = ["concurrency capacity"]
            continue
        counts[job["kind"]] += 1
        selected.append(job)
    return selected, blocked

def update_receipt(plan, state, root, jid, status, receipt_path):
    item = state["jobs"][jid]
    if status not in ALLOWED_TRANSITIONS.get(item["status"], set()):
        raise ValueError("invalid status transition: " + item["status"] + " -> " + status)
    receipt = read_json(receipt_path)
    task_id = receipt.get("task_id")
    if status in {"submitted", "running", "succeeded"} and not task_id:
        raise ValueError("normalized receipt needs task_id; local tasks use local:<id>")
    if item.get("task_id") and task_id and item["task_id"] != task_id:
        raise ValueError("receipt task_id differs from original task")
    if receipt.get("request_key") and receipt["request_key"] != item["request_key"]:
        raise ValueError("receipt request_key mismatch")
    outputs = receipt.get("outputs", [])
    if status == "succeeded":
        if not isinstance(outputs, list) or not outputs:
            raise ValueError("successful receipt needs actual outputs")
        normalized = []
        for output in outputs:
            if not isinstance(output, dict) or not any(output.get(k) for k in ("path", "node_id", "url")):
                raise ValueError("each output needs path, node_id or url")
            if output.get("path") and not resolve(root, output["path"]).is_file():
                raise ValueError("returned local output missing")
            output = dict(output)
            if output.get("path"):
                path = resolve(root, output["path"])
                output["path"] = str(path)
                output["sha256"] = file_hash(path)
            normalized.append(output)
        outputs = normalized
        if item["status"] == "succeeded" and item["outputs"] != outputs:
            raise ValueError("cannot replace a completed result with a different output")
        item["outputs"] = outputs
    item["status"] = status
    if task_id:
        item["task_id"] = task_id
    item["updated_at"] = now()
    record = {"path": str(Path(receipt_path).resolve()), "sha256": file_hash(Path(receipt_path))}
    if record not in item["receipts"]:
        item["receipts"].append(record)
    event(state, jid, "record", {"status": status, "task_id": task_id})

def run(args):
    plan_path = Path(args.plan).resolve()
    root = plan_path.parent
    plan = read_json(plan_path)
    dispatch = args.command in {"audit", "init", "ready", "claim"}
    errors, warnings = validate(plan, root) if dispatch else ([], [])
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}, 2
    try:
        signature, inputs = snapshot(plan, root)
    except (OSError, ValueError, KeyError, TypeError):
        if dispatch:
            raise
        signature, inputs = None, {}
    if args.command == "audit":
        return {"ok": True, "warnings": warnings, "jobs": len(plan["jobs"]),
                "plan_digest": signature, "input_files": len(inputs)}, 0
    state_path = Path(args.state).resolve() if args.state else root / "production-state.json"
    with locked(state_path):
        if args.command == "init":
            if state_path.exists():
                current = read_json(state_path)
                if current.get("plan_digest") != signature:
                    raise ValueError("state already exists for different inputs; preserve it and create a new revision/state")
                return {"ok": True, "existing": True, "state": str(state_path)}, 0
            atomic_write(state_path, init_state(plan, signature))
            return {"ok": True, "state": str(state_path)}, 0
        if not state_path.exists():
            raise ValueError("state missing; run init first")
        state = read_json(state_path)
        changed = state.get("plan_digest") != signature
        if changed and dispatch:
            raise ValueError("plan or input changed; stop dispatch, preserve receipts and prepare a new revision")
        plan = state.get("plan_snapshot", plan)
        if args.command in {"ready", "status", "claim"}:
            selected, blocked = ready_jobs(plan, state, root, getattr(args, "limit", None))
            if changed:
                selected = []
                blocked.update({j["id"]: ["inputs changed; prepare a new revision"]
                                for j in plan["jobs"] if state["jobs"][j["id"]]["status"] == "pending"})
            pending_delivery = []
            for job in plan["jobs"]:
                item = state["jobs"][job["id"]]
                if item["status"] == "succeeded":
                    for channel in job.get("delivery_channels", []):
                        if channel not in item["delivery"]:
                            pending_delivery.append({"job": job["id"], "channel": channel,
                                                     "review": item["review"]})
            if args.command == "claim":
                claimed = []
                for job in selected:
                    item = state["jobs"][job["id"]]
                    item["status"] = "claimed"
                    item["claimed_at"] = now()
                    event(state, job["id"], "claim")
                    request = {"id": job["id"], "kind": job["kind"],
                                    "request_key": item["request_key"],
                                    "prompt_file": str(resolve(root, job["prompt_file"])) if job.get("prompt_file") else None,
                                    "prompt": production_contract.render_prompt(plan, job, resolve(root, job["prompt_file"]).read_text(encoding="utf-8")) if job.get("prompt_file") else None,
                                    "parameters": job.get("parameters", {}),
                                    "duration_seconds": job.get("duration_seconds"),
                                    "references": resolve_refs(job, state, root)}
                    if "director_contract" in plan and job["kind"] == "video":
                        request["director_shots"] = job.get("shots", [])
                        request["transition_in"] = production_contract.transition_for(plan, job["id"], "in")
                        request["transition_out"] = production_contract.transition_for(plan, job["id"], "out")
                        maps = production_contract.used_blocking_states(plan, job)
                        if maps:
                            request["blocking_states"] = maps
                    if request["prompt"] is not None:
                        request["prompt_length_check"] = prompt_guard.check(
                            request["prompt"], request["parameters"].get("model", job.get("model", "")),
                            job.get("prompt_limits"))
                    request_path = state_path.parent / (state_path.stem + "-requests") / (item["request_key"] + ".json")
                    if request_path.exists() and read_json(request_path) != request:
                        raise ValueError("sealed request differs; preserve old request and prepare a new revision")
                    if not request_path.exists():
                        atomic_write(request_path, request)
                    item["request_file"] = str(request_path)
                    claimed.append({k: v for k, v in request.items() if k != "prompt"} | {"request_file": str(request_path)})
                atomic_write(state_path, state)
                return {"ok": True, "claimed": claimed, "blocked": blocked,
                        "pending_delivery": pending_delivery}, 0
            return {"ok": True, "inputs_changed": changed, "ready": [j["id"] for j in selected], "blocked": blocked,
                    "active": [{"id": k, "status": v["status"], "task_id": v.get("task_id"),
                                "request_key": v["request_key"]}
                               for k, v in state["jobs"].items() if v["status"] in ACTIVE],
                    "pending_review": [j["id"] for j in plan["jobs"] if j.get("review_required")
                                       and state["jobs"][j["id"]]["status"] == "succeeded"
                                       and state["jobs"][j["id"]]["review"] == "pending"],
                    "pending_delivery": pending_delivery}, 0
        jid = args.job
        if jid not in state["jobs"]:
            raise ValueError("unknown job " + jid)
        item = state["jobs"][jid]
        if args.command == "record":
            update_receipt(plan, state, root, jid, args.status, Path(args.receipt))
        elif args.command == "review":
            if item["status"] != "succeeded":
                raise ValueError("review requires actual successful output")
            note = Path(args.note_file).read_text(encoding="utf-8").strip()
            if not note:
                raise ValueError("review note is empty")
            item["review"] = args.decision
            item["review_by"] = args.by
            item["review_note"] = note
            if args.observed_state:
                observed = read_json(args.observed_state)
                if not isinstance(observed, dict):
                    raise ValueError("observed state must be an object")
                item["observed_end_state"] = observed
            event(state, jid, "review", {"decision": args.decision, "by": args.by})
        elif args.command == "deliver":
            if item["status"] != "succeeded":
                raise ValueError("cannot deliver an unfinished job")
            evidence = Path(args.evidence_file).read_text(encoding="utf-8").strip()
            if not evidence:
                raise ValueError("delivery evidence is empty")
            item["delivery"][args.channel] = {"at": now(), "evidence": evidence}
            event(state, jid, "deliver", {"channel": args.channel})
        elif args.command == "release":
            if item["status"] != "claimed" or item.get("task_id") or item["receipts"]:
                raise ValueError("release only applies to a claim with no external receipt")
            note = Path(args.not_submitted_evidence).read_text(encoding="utf-8").strip()
            if not note:
                raise ValueError("need evidence that no external request was sent")
            item["status"] = "pending"
            event(state, jid, "release", {"not_submitted_evidence": note})
        atomic_write(state_path, state)
        return {"ok": True, "job": jid, "status": item["status"], "review": item["review"]}, 0

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for cmd in ("audit", "init", "status", "ready", "claim", "record", "review", "deliver", "release"):
        q = sub.add_parser(cmd)
        q.add_argument("plan")
        q.add_argument("--state")
        if cmd == "claim":
            q.add_argument("--limit", type=int)
        if cmd in {"record", "review", "deliver", "release"}:
            q.add_argument("--job", required=True)
        if cmd == "record":
            q.add_argument("--status", choices=("submitted", "running", "succeeded", "failed", "unknown"), required=True)
            q.add_argument("--receipt", required=True)
        if cmd == "review":
            q.add_argument("--decision", choices=("passed", "failed"), required=True)
            q.add_argument("--by", choices=("assistant", "user"), required=True)
            q.add_argument("--note-file", required=True)
            q.add_argument("--observed-state")
        if cmd == "deliver":
            q.add_argument("--channel", choices=sorted(CHANNELS), required=True)
            q.add_argument("--evidence-file", required=True)
        if cmd == "release":
            q.add_argument("--not-submitted-evidence", required=True)
    return p

def main():
    args = parser().parse_args()
    try:
        if getattr(args, "limit", None) is not None and not positive_int(args.limit):
            raise ValueError("limit must be positive")
        result, status = run(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result, status = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status

if __name__ == "__main__":
    sys.exit(main())
