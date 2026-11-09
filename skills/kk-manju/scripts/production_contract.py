#!/usr/bin/env python3
"""Director-to-prompt contracts. Structural checks, never creative approval."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

IN_TOKEN = "{{TRANSITION_IN}}"
OUT_TOKEN = "{{TRANSITION_OUT}}"
SHOT_TOKEN = "{{SHOT_DESIGN}}"
METHODS = {"action_match", "eyeline_match", "sound_bridge", "occlusion_cut",
           "establishing_cut", "time_jump"}


def readable(text):
    """Resolve authoring labels, without inventing provider reference bindings."""
    return re.sub(r"【(?:人物|场景|道具|位置)：([^】]+)】", r"\1", text)


def used_blocking_states(plan, job):
    states = plan.get("director_contract", {}).get("blocking_states", {})
    ids = {shot.get(key) for shot in job.get("shots", [])
           for key in ("start_blocking", "end_blocking") if shot.get(key)}
    return {key: states[key] for key in sorted(ids) if key in states}


def animation_text(t, side, core_start, core_end):
    animation = t.get("animation")
    if not isinstance(animation, dict):
        return ""
    mode = animation.get("mode")
    if mode == "post":
        return "\n转场动画由后期执行；本段保留所需干净运动素材与原表演，模型不重复生成该效果。"
    if mode not in {"generated", "hybrid"}:
        return ""
    seconds = animation[side + "_seconds"]
    a, b = ((core_start, core_start + seconds) if side == "in"
            else (core_end - seconds, core_end))
    instruction = animation[side + "_instruction"]
    return (f"\n[转场动画 {t['id']}｜{a:g}–{b:g}秒]\n{instruction}"
            f"\n视觉运动方向：{animation['direction']}。"
            f"\n转场载体：{animation['carrier']}。"
            f"\n交界画面锚定：{animation['bridge_frame']}。"
            f"\n切换条件：{animation['cut_when']}。"
            + ("\n混合方案的后期部分另行执行，不在模型中重复叠加。" if mode == "hybrid" else ""))


def validate_staging(plan):
    c = plan["director_contract"]
    requirements = c.get("requirements", {})
    if not isinstance(requirements, dict):
        return ["director_contract.requirements must be an object"]
    errors = []
    for name in ("blocking_maps", "transition_animation"):
        if name in requirements and not isinstance(requirements[name], bool):
            errors.append("director_contract.requirements." + name + " must be boolean")
    states = c.get("blocking_states", {})
    if not isinstance(states, dict):
        return errors + ["director_contract.blocking_states must be an object"]
    cast = c.get("cast", {})
    if not isinstance(cast, dict):
        return errors
    for state_id, state in states.items():
        if not isinstance(state, dict) or not isinstance(state.get("diagram"), str) or not state["diagram"].strip():
            errors.append(str(state_id) + ": blocking state needs a text diagram")
            continue
        actors = state.get("actors", {})
        if not isinstance(actors, dict):
            errors.append(str(state_id) + ": blocking actors must be an object")
            continue
        names = set(re.findall(r"【人物：([^】]+)】", state["diagram"]))
        declared = set()
        for actor_id, actor in actors.items():
            if actor_id not in cast:
                errors.append(str(state_id) + ": unknown blocking actor " + str(actor_id))
                continue
            declared.add(cast[actor_id])
            if not isinstance(actor, dict) or any(not isinstance(actor.get(k), str) or not actor[k].strip()
                                                 for k in ("position", "facing")):
                errors.append(str(state_id) + ": actor needs position and facing")
        if names != declared:
            errors.append(str(state_id) + ": diagram characters differ from declared actors")
    videos = {j["id"]: j for j in plan.get("jobs", []) if j.get("kind") == "video"}
    for jid, job in videos.items():
        for shot in job.get("shots", []):
            for key in ("start_blocking", "end_blocking"):
                value = shot.get(key)
                if requirements.get("blocking_maps") or value is not None:
                    if not isinstance(value, str) or value not in states:
                        errors.append(str(shot.get("id")) + ": missing/unknown " + key)
            for name in re.findall(r"【人物：([^】]+)】", shot.get("action", "")):
                if name not in cast.values():
                    errors.append(str(shot.get("id")) + ": unknown character placeholder " + name)
    for t in c.get("transitions", []):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "?"))
        a = t.get("animation")
        if a is None:
            if requirements.get("transition_animation"):
                errors.append(tid + ": transition animation design is required")
            continue
        if not isinstance(a, dict):
            errors.append(tid + ": animation must be an object")
            continue
        mode = a.get("mode")
        if mode not in {"generated", "post", "hybrid"}:
            errors.append(tid + ": animation mode must assign generated, post or hybrid ownership")
        for field in ("style", "direction", "carrier", "bridge_frame", "cut_when"):
            if not isinstance(a.get(field), str) or not a[field].strip():
                errors.append(tid + ": animation missing " + field)
        fields = ("out_instruction", "in_instruction") if mode in {"generated", "hybrid"} else ()
        if mode in {"post", "hybrid"}:
            fields += ("post_instruction",)
        for field in fields:
            if not isinstance(a.get(field), str) or not a[field].strip():
                errors.append(tid + ": animation missing " + field)
        duration, out_s, in_s = (a.get(k) for k in ("duration_seconds", "out_seconds", "in_seconds"))
        if any(not valid_seconds(v) or v <= 0 for v in (duration, out_s, in_s)):
            errors.append(tid + ": animation duration and two phase spans must be positive")
            continue
        if not math.isclose(out_s + in_s, duration, abs_tol=0.001):
            errors.append(tid + ": animation phase spans differ from total duration")
        for jid, index, span, state_key, map_key in (
            (t.get("from_job"), -1, out_s, "end_blocking", "from_blocking"),
            (t.get("to_job"), 0, in_s, "start_blocking", "to_blocking"),
        ):
            shots = videos.get(jid, {}).get("shots", [])
            if not shots:
                errors.append(tid + ": animation endpoint has no shot")
                continue
            shot = shots[index]
            limit = shot.get("duration_seconds")
            if not valid_seconds(limit) or span > limit + 0.001:
                errors.append(tid + ": animation phase exceeds boundary shot")
            if requirements.get("blocking_maps") or map_key in a:
                value = a.get(map_key)
                if not isinstance(value, str) or value not in states or value != shot.get(state_key):
                    errors.append(tid + ": animation " + map_key + " differs from boundary blocking state")
    return errors


def file_path(root, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def inputs(plan):
    contract = plan.get("director_contract", {})
    if not isinstance(contract, dict) or not isinstance(contract.get("inputs", {}), dict):
        return []
    return [v["file"] for v in contract.get("inputs", {}).values()
            if isinstance(v, dict) and isinstance(v.get("file"), str)]


def valid_seconds(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def transition_for(plan, job_id, side):
    key = "to_job" if side == "in" else "from_job"
    contract = plan.get("director_contract", {})
    transitions = contract.get("transitions", []) if isinstance(contract, dict) else []
    if not isinstance(transitions, list):
        return None
    return next((t for t in transitions if isinstance(t, dict) and t.get(key) == job_id), None)


def render_prompt(plan, job, raw):
    if "director_contract" not in plan or job.get("kind") != "video":
        return raw
    cast = plan.get("director_contract", {}).get("cast", {})
    incoming = transition_for(plan, job["id"], "in")
    position = incoming.get("handles", {}).get("in_seconds", 0) if incoming else 0
    core_start = position
    lines = ["[逐镜执行]"]
    modes = {"dialogue": "画内对白", "inner": "内心独白，嘴唇不动", "voice_over": "画外音"}
    for shot in job.get("shots", []):
        end = position + shot["duration_seconds"]
        camera = shot.get("camera", {})
        lines += [f"镜头 {shot['id']}｜{position:g}–{end:g}秒", "画面与表演：" + shot["action"],
                  "摄影：" + camera["framing"] + "；" + camera["movement"],
                  "切镜：" + shot["cut"]]
        for event in shot.get("events", []):
            if event.get("kind") == "dialogue":
                name = cast.get(event.get("speaker"), str(event.get("speaker")))
                voice = modes.get(event.get("voice_mode"), str(event.get("voice_mode")))
                lines.append(f"{position + event['start']:g}–{position + event['end']:g}秒，{name}，{voice}：{event['text']}")
            elif event.get("text"):
                lines.append(f"{position + event['start']:g}–{position + event['end']:g}秒，{event.get('kind', '声音/动作')}：{event['text']}")
        position = end
    replacements = {SHOT_TOKEN: "\n".join(lines)}
    for side, token, field, label in (
        ("in", IN_TOKEN, "head_instruction", "首镜承接"),
        ("out", OUT_TOKEN, "tail_instruction", "尾镜衔接"),
    ):
        t = transition_for(plan, job["id"], side)
        if t is None:
            replacements[token] = ""
            continue
        seconds = t.get("handles", {}).get(side + "_seconds", 0)
        text = f"[{label} {t['id']}]\n{t[field]}"
        if t.get("sound"):
            text += "\n声音承接：" + t["sound"]
        text += animation_text(t, side, core_start, position)
        if seconds:
            text += f"\n保留{seconds:g}秒可裁剪的{'开头' if side == 'in' else '结尾'}运动余量；动作连续，不为余量停顿或重演。"
        replacements[token] = text
    for token, replacement in replacements.items():
        raw = raw.replace(token, replacement)
    return readable(raw)


def validate(plan, root):
    if "director_contract" not in plan:
        return []
    c = plan["director_contract"]
    errors = []
    if not isinstance(c, dict) or c.get("version") != 1:
        return ["director_contract.version must be 1"]
    source_inputs = c.get("inputs", {})
    if not isinstance(source_inputs, dict) or set(source_inputs) != {"script", "design"}:
        return ["director_contract.inputs needs script and design file/hash pairs"]
    for role, item in source_inputs.items():
        if not isinstance(item, dict) or not item.get("file") or not item.get("sha256"):
            errors.append("director_contract: missing " + role + " file/hash")
            continue
        path = file_path(root, item["file"])
        if not path.is_file():
            errors.append("director_contract: " + role + " file missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            errors.append("director_contract: " + role + " changed; revise design/prompts before dispatch")
    cast = c.get("cast", {})
    if not isinstance(cast, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                        or not k or not v for k, v in cast.items()):
        errors.append("director_contract.cast must map role ids to canonical names")
        cast = {}
    forbidden = c.get("forbidden_terms", [])
    if not isinstance(forbidden, list) or any(not isinstance(t, str) or not t for t in forbidden):
        errors.append("director_contract.forbidden_terms must be non-empty strings")
        forbidden = []
    videos = {j["id"]: j for j in plan.get("jobs", []) if j.get("kind") == "video"}
    sequence = c.get("video_order")
    if (not isinstance(sequence, list) or any(not isinstance(i, str) for i in sequence)
            or len(sequence) != len(set(sequence)) or set(sequence) != set(videos)):
        return errors + ["director_contract.video_order must cover every video job exactly once"]
    actual_shots = []
    for jid in sequence:
        job = videos[jid]
        shots = job.get("shots", [])
        if not shots:
            errors.append(jid + ": director contract needs designed shots")
        for shot in shots:
            sid = shot.get("id", "?")
            actual_shots.append(sid)
            for field in ("purpose", "action", "cut"):
                if not isinstance(shot.get(field), str) or not shot[field].strip():
                    errors.append(str(sid) + ": missing director field " + field)
            camera = shot.get("camera", {})
            if not isinstance(camera, dict) or any(not camera.get(k) for k in
                                                  ("framing", "movement", "motivation")):
                errors.append(str(sid) + ": camera needs framing, movement and motivation")
            for event in shot.get("events", []):
                if event.get("kind") == "dialogue":
                    for term in forbidden:
                        if term in event.get("text", ""):
                            errors.append(str(sid) + ": forbidden name/term in dialogue: " + term)
                    speaker = event.get("speaker")
                    if speaker not in cast:
                        errors.append(str(sid) + ": dialogue speaker not in canonical cast")
                    elif event.get("speaker_name", cast[speaker]) != cast[speaker]:
                        errors.append(str(sid) + ": dialogue speaker_name differs from canonical name")
                    if event.get("voice_mode") not in {"dialogue", "inner", "voice_over"}:
                        errors.append(str(sid) + ": dialogue needs explicit voice_mode")
        if job.get("prompt_file"):
            path = file_path(root, job["prompt_file"])
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                if raw.count(SHOT_TOKEN) != 1:
                    errors.append(jid + ": prompt needs one SHOT_DESIGN compilation slot")
                if "[STYLE LOCK]" not in raw:
                    errors.append(jid + ": prompt missing STYLE LOCK")
                try:
                    compiled = render_prompt(plan, job, raw)
                except (KeyError, TypeError, ValueError, AttributeError):
                    compiled = raw  # Missing director fields are reported above.
                for term in forbidden:
                    if term in compiled:
                        errors.append(jid + ": forbidden name/term in compiled prompt: " + term)
                for side, token in (("in", IN_TOKEN), ("out", OUT_TOKEN)):
                    expected = transition_for(plan, jid, side) is not None
                    if raw.count(token) > 1 or (expected and raw.count(token) != 1):
                        errors.append(jid + ": transition template slot missing or repeated: " + token)
    if c.get("shot_order") != actual_shots:
        errors.append("director_contract.shot_order differs from planned shot coverage/order")
    transitions = c.get("transitions", [])
    if not isinstance(transitions, list) or any(not isinstance(t, dict) for t in transitions):
        return errors + ["director_contract.transitions must be a list of objects"]
    wanted = list(zip(sequence, sequence[1:]))
    pairs = [(t.get("from_job"), t.get("to_job")) for t in transitions]
    if pairs != wanted:
        errors.append("director_contract: every adjacent video pair needs one ordered transition")
    ids = [t.get("id") for t in transitions]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        errors.append("director_contract: transition ids must be unique non-empty strings")
    allocated = {jid: 0.0 for jid in sequence}
    for t in transitions:
        tid = str(t.get("id", "?"))
        a, b = t.get("from_job"), t.get("to_job")
        if a not in videos or b not in videos:
            errors.append(tid + ": unknown video endpoint")
            continue
        if t.get("method") not in METHODS:
            errors.append(tid + ": unknown transition method")
        for field in ("tail_instruction", "head_instruction", "reason"):
            if not isinstance(t.get(field), str) or not t[field].strip():
                errors.append(tid + ": missing transition " + field)
        if t.get("method") == "sound_bridge" and not t.get("sound"):
            errors.append(tid + ": sound bridge needs a sound instruction")
        keys = t.get("state_keys")
        if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys):
            errors.append(tid + ": state_keys must explicitly name carried state")
            keys = []
        changes = t.get("intentional_changes", {})
        if not isinstance(changes, dict):
            errors.append(tid + ": intentional_changes must be an object of reasons")
            changes = {}
        for key in keys:
            end, start = videos[a].get("end_state", {}), videos[b].get("start_state", {})
            if key not in end or key not in start:
                errors.append(tid + ": handoff state missing: " + key)
            elif end[key] != start[key] and not (
                    isinstance(changes.get(key), str) and changes[key].strip()):
                errors.append(tid + ": unmotivated state change: " + key)
        handles = t.get("handles", {})
        if not isinstance(handles, dict):
            errors.append(tid + ": handles must be an object")
            handles = {}
        for side, jid in (("out", a), ("in", b)):
            value = handles.get(side + "_seconds", 0)
            if not valid_seconds(value):
                errors.append(tid + ": invalid transition handle")
            else:
                allocated[jid] += value
    for jid, seconds in allocated.items():
        available = videos[jid].get("handles_seconds", 0)
        if not valid_seconds(available) or seconds > available + 0.001:
            errors.append(jid + ": transition handles exceed planned generated footage margin")
    errors.extend(validate_staging(plan))
    return errors


def compile_bundle(plan_path, output):
    """Produce reviewable final prompts/maps/edit notes; no claim, network or generation."""
    import pipeline
    plan_path = Path(plan_path).resolve()
    root = plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    errors, warnings = pipeline.validate(plan, root)
    if errors:
        raise ValueError("; ".join(errors))
    c = plan.get("director_contract")
    if not isinstance(c, dict):
        raise ValueError("compile requires a director_contract")
    ids = c["video_order"]
    if any(not jid or jid in {".", ".."} or any(ch in jid for ch in "/\\\x00") for jid in ids):
        raise ValueError("video job id is not a safe filename")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("preserve the previous bundle; use a new output directory")
    signature, _ = pipeline.snapshot(plan, root)
    output.mkdir(parents=True)
    prompts = output / "prompts"
    prompts.mkdir()
    rows = []
    notes = ["# 调度图与转场执行单", "", "这是计划编译包，未提交生成、未渲染转场，不代表画面验收。", ""]
    jobs = {j["id"]: j for j in plan["jobs"]}
    for jid in ids:
        job = jobs[jid]
        raw = file_path(root, job["prompt_file"]).read_text(encoding="utf-8")
        final = render_prompt(plan, job, raw)
        path = prompts / (jid + ".txt")
        path.write_text(final, encoding="utf-8")
        rows.append({"job": jid, "prompt": str(path), "sha256": hashlib.sha256(final.encode()).hexdigest()})
        notes += ["## " + jid, ""]
        for sid, state in used_blocking_states(plan, job).items():
            notes += ["### 站位 " + sid, "", "```text", state["diagram"], "```", ""]
        for shot in job.get("shots", []):
            notes += [f"- {shot['id']}：{shot.get('start_blocking', '未指定')} → {shot.get('end_blocking', '未指定')}；{shot['purpose']}"]
        notes.append("")
    for t in c.get("transitions", []):
        notes += [f"## 转场 {t['id']}：{t['from_job']} → {t['to_job']}", "",
                  "前段尾镜：" + t["tail_instruction"], "", "后段首镜：" + t["head_instruction"], ""]
        if t.get("animation"):
            notes += ["动画计划：", "", "```json", json.dumps(t["animation"], ensure_ascii=False, indent=2), "```", ""]
    notes_path = output / "调度与转场.md"
    notes_path.write_text("\n".join(notes), encoding="utf-8")
    manifest = {"ok": True, "plan": str(plan_path), "plan_digest": signature,
                "files": rows, "staging_and_transitions": str(notes_path), "warnings": warnings,
                "boundary": "Compiled only; no paid submission, rendering or artistic approval."}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["compile"])
    parser.add_argument("plan")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = compile_bundle(args.plan, args.output)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
