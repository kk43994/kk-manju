#!/usr/bin/env python3
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import pipeline
import asset_inventory
import media_probe

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan_path = self.root / "plan.json"
        (self.root / "prompt.md").write_text("synthetic test prompt", encoding="utf-8")
        self.plan = {"schema_version": 1, "project_id": "test", "revision": "r1",
                     "authorized_kinds": ["image", "video", "edit", "sample"],
                     "limits": {"total": 2, "by_kind": {"image": 2}},
                     "jobs": [self.job("a"), self.job("b"), self.job("c")]}

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, jid, **values):
        result = {"id": jid, "kind": "image", "prompt_file": "prompt.md",
                  "depends_on": [], "review_required": False, "references": []}
        result.update(values)
        return result

    def write(self):
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def cli(self, command, *args, expect=0):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "pipeline.py"), command,
                               str(self.plan_path), *args], capture_output=True, text=True)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def initialize(self):
        self.write()
        self.cli("init")

    def receipt(self, jid, status="succeeded", outputs=None):
        data = {"task_id": "local:" + jid}
        if status == "succeeded":
            data["outputs"] = outputs or [{"node_id": "test-node-" + jid}]
        path = self.root / (jid + "-" + status + ".json")
        path.write_text(json.dumps(data), encoding="utf-8")
        return self.cli("record", "--job", jid, "--status", status, "--receipt", str(path))

    def test_cycles_and_missing_dependencies(self):
        self.plan["jobs"][0]["depends_on"] = ["b"]
        self.plan["jobs"][1]["depends_on"] = ["a"]
        self.assertTrue(any("cycle" in e for e in pipeline.validate(self.plan, self.root)[0]))
        self.plan["jobs"][0]["depends_on"] = ["absent"]
        self.assertTrue(any("dependency missing" in e for e in pipeline.validate(self.plan, self.root)[0]))

    def test_concurrent_claims_never_exceed_capacity_or_duplicate(self):
        self.initialize()
        def claim(_):
            return self.cli("claim", "--limit", "2")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(claim, range(3)))
        ids = [j["id"] for r in results for j in r["claimed"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(len(self.cli("status")["active"]), 2)

    def test_unknown_is_not_automatically_released_or_resubmitted(self):
        self.plan["limits"]["total"] = 1
        self.initialize()
        first = self.cli("claim")["claimed"][0]
        receipt = self.root / "unknown.json"
        receipt.write_text('{"reason":"submission response lost"}')
        self.cli("record", "--job", "a", "--status", "unknown", "--receipt", str(receipt))
        self.assertEqual(self.cli("claim")["claimed"], [])
        status = self.cli("status")
        self.assertEqual(status["active"][0]["status"], "unknown")
        self.assertEqual(status["active"][0]["request_key"], first["request_key"])
        note = self.root / "note.md"
        note.write_text("cannot prove no external submission")
        self.cli("release", "--job", "a", "--not-submitted-evidence", str(note), expect=2)

    def test_review_and_actual_outputs_unlock_dependency(self):
        self.plan["jobs"] = [
            self.job("a", review_required=True),
            self.job("b", depends_on=["a"], references=[{"role": "identity", "source_job": "a"}])]
        self.initialize()
        self.assertEqual([j["id"] for j in self.cli("claim")["claimed"]], ["a"])
        output = self.root / "asset.png"
        output.write_bytes(b"synthetic asset bytes")
        self.receipt("a", outputs=[{"path": "asset.png"}])
        self.assertEqual(self.cli("ready")["ready"], [])
        note = self.root / "review.md"
        note.write_text("Test-only review evidence; no visual quality claim")
        self.cli("review", "--job", "a", "--decision", "passed", "--by", "assistant",
                 "--note-file", str(note))
        claimed = self.cli("claim")["claimed"]
        self.assertEqual(claimed[0]["id"], "b")
        self.assertEqual(Path(claimed[0]["references"][0]["resolved_output"]["path"]), output.resolve())

    def test_changed_output_blocks_dependent_task(self):
        self.plan["jobs"] = [
            self.job("a"),
            self.job("b", depends_on=["a"], references=[{"role": "identity", "source_job": "a"}])]
        self.initialize()
        self.cli("claim")
        output = self.root / "asset.png"
        output.write_bytes(b"v1")
        self.receipt("a", outputs=[{"path": "asset.png"}])
        output.write_bytes(b"v2")
        self.assertEqual(self.cli("ready")["ready"], [])
        self.assertIn("changed", " ".join(self.cli("ready")["blocked"]["b"]))

    def test_changed_prompt_blocks_dispatch_and_keeps_receipts(self):
        self.initialize()
        self.cli("claim", "--limit", "1")
        self.receipt("a", "submitted")
        before = (self.root / "production-state.json").read_bytes()
        (self.root / "prompt.md").write_text("new version")
        self.assertIn("changed", self.cli("claim", expect=2)["error"])
        self.assertEqual(before, (self.root / "production-state.json").read_bytes())
        self.assertTrue(self.cli("status")["inputs_changed"])
        self.receipt("a", "succeeded")
        state = json.loads((self.root / "production-state.json").read_text())
        self.assertEqual(state["jobs"]["a"]["status"], "succeeded")
        sealed = json.loads(Path(state["jobs"]["a"]["request_file"]).read_text())
        self.assertEqual(sealed["prompt"], "synthetic test prompt")

    def test_dialogue_and_action_can_overlap_but_required_order_cannot(self):
        self.plan["dialogue_source"] = {"d1": "这是真实的原句。"}
        self.plan["jobs"] = [self.job("a", kind="video", duration_seconds=5,
            shots=[{"id": "s1", "duration_seconds": 5, "events": [
                {"id": "voice", "kind": "dialogue", "source_id": "d1", "text": "这是真实的原句。",
                 "start": 0, "end": 3},
                {"id": "move", "kind": "action", "start": 0, "end": 5}]}])]
        self.assertEqual(pipeline.validate(self.plan, self.root)[0], [])
        self.plan["jobs"][0]["shots"][0]["events"][1]["after"] = ["voice"]
        self.assertTrue(any("overlap" in e for e in pipeline.validate(self.plan, self.root)[0]))
        self.plan["jobs"][0]["shots"][0]["events"][0]["text"] = "偷偷改写了。"
        self.assertTrue(any("coverage" in e for e in pipeline.validate(self.plan, self.root)[0]))

    def test_continuation_requires_dependency_and_reference(self):
        self.plan["jobs"][1]["continuity"] = {"from_job": "a", "mode": "continuation"}
        errors = pipeline.validate(self.plan, self.root)[0]
        self.assertTrue(any("must depend" in e for e in errors))
        self.assertTrue(any("actual prior-output" in e for e in errors))

    def test_review_failure_preserves_generated_result_and_blocks_child(self):
        self.plan["jobs"] = [self.job("a", review_required=True), self.job("b", depends_on=["a"])]
        self.initialize()
        self.cli("claim")
        self.receipt("a")
        note = self.root / "review.md"
        note.write_text("Visible continuity mismatch in synthetic fixture")
        self.cli("review", "--job", "a", "--decision", "failed", "--by", "assistant", "--note-file", str(note))
        self.assertEqual(self.cli("ready")["ready"], [])
        state = json.loads((self.root / "production-state.json").read_text())
        self.assertEqual(state["jobs"]["a"]["status"], "succeeded")
        self.assertTrue(state["jobs"]["a"]["outputs"])

    def test_chat_and_canvas_delivery_are_independent(self):
        self.plan["jobs"] = [self.job("a", delivery_channels=["chat", "canvas"])]
        self.initialize()
        self.cli("claim")
        self.receipt("a")
        self.assertEqual(len(self.cli("status")["pending_delivery"]), 2)
        evidence = self.root / "chat.md"
        evidence.write_text("Test chat display receipt")
        self.cli("deliver", "--job", "a", "--channel", "chat", "--evidence-file", str(evidence))
        self.assertEqual(self.cli("status")["pending_delivery"][0]["channel"], "canvas")
        self.assertEqual(len(self.cli("status")["pending_delivery"]), 1)

    def test_ambiguous_multi_output_requires_explicit_index(self):
        self.plan["jobs"] = [self.job("a"), self.job("b", depends_on=["a"],
                              references=[{"role": "identity", "source_job": "a"}])]
        self.initialize()
        self.cli("claim")
        self.receipt("a", outputs=[{"node_id": "one"}, {"node_id": "two"}])
        self.assertEqual(self.cli("ready")["ready"], [])
        self.assertIn("output_index", " ".join(self.cli("ready")["blocked"]["b"]))

    def test_plan_authorization_and_per_kind_limit(self):
        self.plan["authorized_kinds"] = ["video"]
        self.initialize()
        self.assertEqual(self.cli("claim")["claimed"], [])
        other = json.loads(json.dumps(self.plan))
        other["authorized_kinds"] = ["image"]
        other["limits"]["by_kind"]["image"] = 1
        state = pipeline.init_state(other, "test")
        selected, _ = pipeline.ready_jobs(other, state, self.root)
        self.assertEqual(len(selected), 1)

    def test_actual_continuity_mismatch_blocks_successor(self):
        self.plan["jobs"] = [
            self.job("a", end_state={"actor.right_hand": "cup"}),
            self.job("b", depends_on=["a"], start_state={"actor.right_hand": "cup"},
                     continuity={"from_job": "a", "mode": "cut", "match_keys": ["actor.right_hand"],
                                 "require_actual_state": True})]
        self.initialize()
        self.cli("claim")
        self.receipt("a")
        self.assertEqual(self.cli("ready")["ready"], [])
        note = self.root / "review.md"
        note.write_text("The actual output has an empty right hand")
        observed = self.root / "observed.json"
        observed.write_text('{"actor.right_hand":"empty"}')
        self.cli("review", "--job", "a", "--decision", "passed", "--by", "assistant",
                 "--note-file", str(note), "--observed-state", str(observed))
        self.assertIn("actual continuity mismatch", " ".join(self.cli("ready")["blocked"]["b"]))

    def test_receipt_cannot_switch_task_or_fake_missing_output(self):
        self.initialize()
        self.cli("claim", "--limit", "1")
        self.receipt("a", "submitted")
        path = self.root / "wrong.json"
        path.write_text('{"task_id":"another-task","outputs":[{"node_id":"fake"}]}')
        self.cli("record", "--job", "a", "--status", "succeeded", "--receipt", str(path), expect=2)
        path.write_text('{"task_id":"local:a","outputs":[{"path":"does-not-exist.mp4"}]}')
        self.cli("record", "--job", "a", "--status", "succeeded", "--receipt", str(path), expect=2)

class MediaTests(unittest.TestCase):
    def test_inventory_duplicates_do_not_mean_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.png").write_bytes(b"identical")
            (root / "two.png").write_bytes(b"identical")
            result = asset_inventory.scan([root])
            self.assertEqual(len(result["duplicates"]), 1)
            self.assertTrue(all(a["selection_status"] == "unselected" for a in result["assets"]))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg unavailable")
    def test_real_local_probe_and_frame_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "fixture.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:size=96x64:rate=12", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=16000", "-t", "1.2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                           capture_output=True, check=True)
            info = media_probe.probe(video)
            self.assertEqual((info["video"]["width"], info["video"]["height"]), (96, 64))
            self.assertTrue(info["audio"])
            output = media_probe.sample(info, root / "frames")
            self.assertEqual(len(output["frames"]), 3)
            self.assertTrue(all(Path(f["path"]).stat().st_size > 0 for f in output["frames"]))

if __name__ == "__main__":
    unittest.main()
