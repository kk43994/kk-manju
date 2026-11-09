#!/usr/bin/env python3
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pipeline
import production_contract as contract


class DirectorContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("script.md", "design.md"):
            (self.root / name).write_text("synthetic approved source " + name)
        (self.root / "a.md").write_text("[STYLE LOCK]\n{{TRANSITION_IN}}\n{{SHOT_DESIGN}}\n{{TRANSITION_OUT}}")
        (self.root / "b.md").write_text("[STYLE LOCK]\n{{TRANSITION_IN}}\n{{SHOT_DESIGN}}\n{{TRANSITION_OUT}}")
        def shot(sid):
            return {"id": sid, "duration_seconds": 5, "purpose": "test-visible change",
                    "camera": {"framing": "medium", "movement": "fixed", "motivation": "read contact"},
                    "action": "hand rises to chest and keeps moving", "cut": "cut on hand rise",
                    "events": []}
        self.plan = {"schema_version": 1, "project_id": "test", "revision": "r1",
            "authorized_kinds": ["video"], "limits": {"total": 2}, "jobs": [
                {"id": "a", "kind": "video", "prompt_file": "a.md", "depends_on": [],
                 "duration_seconds": 6, "handles_seconds": 1, "shots": [shot("SH01")],
                 "end_state": {"hand": "rising at chest", "vial_cm": 5}},
                {"id": "b", "kind": "video", "prompt_file": "b.md", "depends_on": [],
                 "duration_seconds": 6, "handles_seconds": 1, "shots": [shot("SH02")],
                 "start_state": {"hand": "rising at chest", "vial_cm": 5}}],
            "director_contract": {"version": 1, "inputs": {
                role: {"file": name, "sha256": hashlib.sha256((self.root / name).read_bytes()).hexdigest()}
                for role, name in (("script", "script.md"), ("design", "design.md"))},
                "cast": {"ye": "叶宇", "shi": "师心水"}, "forbidden_terms": ["师心宇"],
                "video_order": ["a", "b"], "shot_order": ["SH01", "SH02"],
                "transitions": [{"id": "T01", "from_job": "a", "to_job": "b",
                    "method": "action_match", "reason": "preserve hand action",
                    "tail_instruction": "右手上提至胸前，仍在运动中切出。",
                    "head_instruction": "从右手胸前的上提中段接入，继续动作，不从垂手重做。",
                    "sound": "衣料声连续，不重复对白。", "state_keys": ["hand", "vial_cm"],
                    "handles": {"out_seconds": 0.4, "in_seconds": 0.4}}]}}

    def tearDown(self):
        self.temp.cleanup()

    def errors(self):
        return pipeline.validate(self.plan, self.root)[0]

    def enable_staging_animation(self):
        c = self.plan["director_contract"]
        c["requirements"] = {"blocking_maps": True, "transition_animation": True}
        c["blocking_states"] = {
            "G0": {"diagram": "北／门内\n【人物：叶宇】↓\n────门槛────\n【摄影机A】↖",
                   "actors": {"ye": {"position": "门内", "facing": "南"}}},
            "G1": {"diagram": "北／门内\n────门槛────\n【人物：叶宇】↓\n【摄影机A】↖",
                   "actors": {"ye": {"position": "门外平台", "facing": "南"}}},
        }
        for j in self.plan["jobs"]:
            j["shots"][0].update(start_blocking="G1", end_blocking="G1")
        self.plan["jobs"][0]["shots"][0]["start_blocking"] = "G0"
        c["transitions"][0]["animation"] = {
            "mode": "generated", "style": "foreground_occlusion", "duration_seconds": 0.6,
            "out_seconds": 0.3, "in_seconds": 0.3, "direction": "画面右向左",
            "carrier": "近景门柱与门框", "bridge_frame": "暖黑近景遮满画面，仍向左运动",
            "cut_when": "遮满全幅瞬间", "from_blocking": "G1", "to_blocking": "G1",
            "out_instruction": "近景门柱由右向左扫入至遮满。",
            "in_instruction": "从同色遮满状态接入，遮挡继续向左扫出。",
        }

    def test_valid_contract_and_opposite_prompt_halves(self):
        self.assertEqual(self.errors(), [])
        prompts = [contract.render_prompt(self.plan, j, (self.root / j["prompt_file"]).read_text())
                   for j in self.plan["jobs"]]
        self.assertIn("仍在运动中切出", prompts[0])
        self.assertNotIn("从右手胸前", prompts[0])
        self.assertIn("从右手胸前", prompts[1])
        self.assertNotIn("TRANSITION_", "".join(prompts))

    def test_new_requirements_reject_omitted_animation_and_maps(self):
        self.plan["director_contract"]["requirements"] = {
            "blocking_maps": True, "transition_animation": True}
        errors = self.errors()
        self.assertTrue(any("start_blocking" in e for e in errors))
        self.assertTrue(any("animation design is required" in e for e in errors))

    def test_staged_animation_contract_passes_and_both_phases_compile(self):
        self.enable_staging_animation()
        self.assertEqual(self.errors(), [])
        prompts = [contract.render_prompt(self.plan, j, (self.root / j["prompt_file"]).read_text())
                   for j in self.plan["jobs"]]
        self.assertIn("4.7–5秒", prompts[0])
        self.assertIn("0.4–0.7秒", prompts[1])
        self.assertIn("扫入至遮满", prompts[0])
        self.assertNotIn("遮挡继续向左扫出", prompts[0])
        self.assertIn("遮挡继续向左扫出", prompts[1])
        for p in prompts:
            self.assertIn("暖黑近景遮满画面，仍向左运动", p)

    def test_blocking_typo_cannot_pass_by_using_an_existing_state_id(self):
        self.enable_staging_animation()
        self.plan["director_contract"]["blocking_states"]["G0"]["diagram"] = "【人物：师心宇】"
        self.assertTrue(any("diagram characters differ" in e for e in self.errors()))

    def test_unknown_character_placeholder_rejected(self):
        self.enable_staging_animation()
        self.plan["jobs"][0]["shots"][0]["action"] = "【人物：陌生角色】抬手"
        self.assertTrue(any("unknown character placeholder" in e for e in self.errors()))

    def test_animation_must_reference_actual_boundary_map(self):
        self.enable_staging_animation()
        self.plan["director_contract"]["transitions"][0]["animation"]["from_blocking"] = "G0"
        self.assertTrue(any("differs from boundary blocking" in e for e in self.errors()))

    def test_animation_timing_has_one_total_and_fits_boundary_shots(self):
        self.enable_staging_animation()
        a = self.plan["director_contract"]["transitions"][0]["animation"]
        a.update(out_seconds=6, in_seconds=0.3, duration_seconds=6.3)
        self.assertTrue(any("exceeds boundary shot" in e for e in self.errors()))
        a.update(out_seconds=0.3, duration_seconds=1)
        self.assertTrue(any("phase spans differ" in e for e in self.errors()))

    def test_post_animation_is_not_generated_twice(self):
        self.enable_staging_animation()
        a = self.plan["director_contract"]["transitions"][0]["animation"]
        a.update(mode="post", post_instruction="EDITOR_ONLY_APPLY_WIPE")
        del a["in_instruction"]
        del a["out_instruction"]
        self.assertEqual(self.errors(), [])
        rendered = contract.render_prompt(self.plan, self.plan["jobs"][0], "{{TRANSITION_OUT}}")
        self.assertIn("由后期执行", rendered)
        self.assertNotIn("EDITOR_ONLY_APPLY_WIPE", rendered)

    def test_hybrid_requires_a_separate_post_responsibility(self):
        self.enable_staging_animation()
        self.plan["director_contract"]["transitions"][0]["animation"]["mode"] = "hybrid"
        self.assertTrue(any("post_instruction" in e for e in self.errors()))

    def test_labels_resolve_but_provider_node_references_remain(self):
        self.enable_staging_animation()
        self.plan["jobs"][0]["shots"][0]["action"] = "【人物：叶宇】在【场景：府门】抬手"
        rendered = contract.render_prompt(self.plan, self.plan["jobs"][0], "{{Node source-id}}\n{{SHOT_DESIGN}}")
        self.assertIn("叶宇在府门抬手", rendered)
        self.assertNotIn("【人物：", rendered)
        self.assertIn("{{Node source-id}}", rendered)

    def test_compile_bundle_exports_maps_prompts_and_post_plan_without_dispatch(self):
        self.enable_staging_animation()
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        output = self.root / "compiled"
        manifest = contract.compile_bundle(path, output)
        self.assertTrue(manifest["ok"])
        self.assertEqual(len(manifest["files"]), 2)
        notes = (output / "调度与转场.md").read_text()
        self.assertIn("【人物：叶宇】", notes)
        self.assertIn("foreground_occlusion", notes)
        self.assertNotIn("【人物：", (output / "prompts" / "a.txt").read_text())
        self.assertFalse((self.root / "production-state.json").exists())
        with self.assertRaises(ValueError):
            contract.compile_bundle(path, output)

    def test_sealed_request_keeps_blocking_maps_for_review(self):
        self.enable_staging_animation()
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        pipeline.run(pipeline.parser().parse_args(["init", str(path)]))
        result, _ = pipeline.run(pipeline.parser().parse_args(["claim", str(path), "--limit", "1"]))
        sealed = json.loads(Path(result["claimed"][0]["request_file"]).read_text())
        self.assertEqual(set(sealed["blocking_states"]), {"G0", "G1"})
        self.assertEqual(sealed["transition_out"]["animation"]["mode"], "generated")

    def test_changed_script_stops_dispatch(self):
        (self.root / "script.md").write_text("new dialogue revision")
        self.assertTrue(any("script changed" in e for e in self.errors()))

    def test_missing_or_reordered_shot_is_rejected(self):
        self.plan["director_contract"]["shot_order"].reverse()
        self.assertTrue(any("shot coverage/order" in e for e in self.errors()))

    def test_prop_scale_and_handoff_change_is_rejected(self):
        self.plan["jobs"][1]["start_state"]["vial_cm"] = 15
        self.assertTrue(any("unmotivated state change: vial_cm" in e for e in self.errors()))

    def test_intentional_geography_change_requires_reason(self):
        t = self.plan["director_contract"]["transitions"][0]
        t["method"] = "time_jump"
        self.plan["jobs"][1]["start_state"]["hand"] = "resting on lap"
        t["intentional_changes"] = {"hand": "明确换场省略入座，开场全景交代新的坐姿。"}
        self.assertEqual(self.errors(), [])

    def test_no_missing_video_boundary(self):
        self.plan["director_contract"]["transitions"] = []
        self.assertTrue(any("adjacent video" in e for e in self.errors()))

    def test_handle_budget_not_counted_twice(self):
        self.plan["director_contract"]["transitions"][0]["handles"]["out_seconds"] = 2
        self.assertTrue(any("footage margin" in e for e in self.errors()))

    def test_missing_prompt_slot_blocks(self):
        (self.root / "b.md").write_text("shot B without incoming interface")
        self.assertTrue(any("template slot" in e for e in self.errors()))

    def test_missing_director_action_reports_error(self):
        del self.plan["jobs"][0]["shots"][0]["action"]
        self.assertTrue(any("missing director field action" in e for e in self.errors()))

    def test_sound_events_are_not_lost_in_compilation(self):
        self.plan["jobs"][0]["shots"][0]["events"] = [
            {"id": "sfx", "kind": "sfx", "start": 1, "end": 2, "text": "瓶落进手掌的轻响"}]
        self.assertEqual(self.errors(), [])
        rendered = contract.render_prompt(self.plan, self.plan["jobs"][0], "{{SHOT_DESIGN}}")
        self.assertIn("瓶落进手掌的轻响", rendered)

    def test_final_expanded_prompt_is_measured(self):
        self.plan["jobs"][0]["prompt_limits"] = {"max_chars": 100, "source": "test budget"}
        self.assertLess(len((self.root / "a.md").read_text()), 100)
        self.assertTrue(self.errors())

    def test_misspelled_name_and_unknown_speaker(self):
        event = {"id": "d1", "kind": "dialogue", "start": 0, "end": 3,
                 "source_id": "d1", "text": "师心宇", "speaker": "unknown", "voice_mode": "inner"}
        self.plan["dialogue_source"] = {"d1": "师心宇"}
        self.plan["jobs"][0]["shots"][0]["events"] = [event]
        self.assertTrue(any("forbidden name" in e for e in self.errors()))
        self.assertTrue(any("canonical cast" in e for e in self.errors()))

    def test_sealed_request_contains_actual_compiled_transition(self):
        path = self.root / "plan.json"
        path.write_text(json.dumps(self.plan))
        pipeline.run(pipeline.parser().parse_args(["init", str(path)]))
        result, code = pipeline.run(pipeline.parser().parse_args(["claim", str(path), "--limit", "1"]))
        self.assertEqual(code, 0)
        sealed = json.loads(Path(result["claimed"][0]["request_file"]).read_text())
        self.assertIn("仍在运动中切出", sealed["prompt"])
        self.assertEqual(sealed["transition_out"]["id"], "T01")
        self.assertEqual(sealed["director_shots"][0]["id"], "SH01")
        self.assertIn("hand rises to chest", sealed["prompt"])


if __name__ == "__main__":
    unittest.main()
