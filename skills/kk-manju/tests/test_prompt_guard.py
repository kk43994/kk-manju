import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import prompt_guard
import pipeline

class PromptGuardTests(unittest.TestCase):
    def test_working_budget_boundary_and_no_truncation(self):
        self.assertTrue(prompt_guard.check("a"*900, "mj-v8.1")["ok"])
        result = prompt_guard.check("a"*901, "mj-v8.1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["counts"]["characters"], 901)
        self.assertEqual(result["constraints"][0]["kind"], "working_budget")

    def test_unicode_units_are_not_confused(self):
        counts = prompt_guard.check("中😀")["counts"]
        self.assertEqual(counts["characters"], 2)
        self.assertEqual(counts["utf16_units"], 3)
        self.assertEqual(counts["utf8_bytes"], 7)

    def test_declared_limit_and_budget_both_apply(self):
        result = prompt_guard.check("a"*801, "mj-v8.1",
                                    {"max_chars": 800, "source": "synthetic test contract"})
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["constraints"]), 2)
        self.assertTrue(any("declared_limit exceeded" in e for e in result["errors"]))

    def test_byte_limit_can_reject_short_character_text(self):
        result = prompt_guard.check("中文", limits={"max_utf8_bytes": 5, "source": "test"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["counts"]["characters"], 2)

    def test_tokens_need_an_exact_count(self):
        self.assertFalse(prompt_guard.check("a b", limits={"max_tokens": 4, "source": "test"})["ok"])
        self.assertTrue(prompt_guard.check("a b", limits={"max_tokens": 4, "token_count": 3, "source": "test"})["ok"])
        self.assertFalse(prompt_guard.check("a b", limits={"max_tokens": 4, "token_count": 5, "source": "test"})["ok"])

    def test_bad_limit_configuration_does_not_silently_pass(self):
        for limits in [{"max_chars": 10}, {"max_chars": True, "source": "test"},
                       {"max_chars": -1, "source": "test"}, {"max_chras": 4}, []]:
            self.assertFalse(prompt_guard.check("abc", limits=limits)["ok"])

class PipelineLengthIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "plan.json"
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("a"*800)
        self.plan = {"schema_version":1, "project_id":"test", "revision":"r1",
                     "authorized_kinds":["image"], "limits":{"total":1},
                     "jobs":[{"id":"style", "kind":"image", "depends_on":[],
                              "prompt_file":"prompt.txt", "parameters":{"model":"mj-v8.1"},
                              "references":[], "review_required":False, "delivery_channels":[]}]}
        self.path.write_text(json.dumps(self.plan))

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, command, **kwargs):
        args = argparse.Namespace(command=command, plan=str(self.path),
                                  state=str(self.root/"state.json"), **kwargs)
        return pipeline.run(args)

    def test_oversize_prompt_is_blocked_before_queue_state_exists(self):
        self.prompt.write_text("a"*901)
        result, code = self.run_command("init")
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertFalse((self.root/"state.json").exists())

    def test_sealed_request_records_actual_length(self):
        self.run_command("init")
        result, code = self.run_command("claim", limit=1)
        self.assertEqual(code, 0)
        sealed = json.loads(Path(result["claimed"][0]["request_file"]).read_text())
        self.assertEqual(sealed["prompt_length_check"]["counts"]["characters"], 800)

    def test_accepted_task_recovery_survives_later_prompt_length_change(self):
        self.run_command("init")
        self.run_command("claim", limit=1)
        self.prompt.write_text("a"*901)
        receipt = self.root/"receipt.json"
        receipt.write_text(json.dumps({"task_id":"provider-accepted-1","outputs":[{"node_id":"real-result"}]}))
        result, code = self.run_command("record", job="style", status="succeeded", receipt=str(receipt))
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        state = json.loads((self.root/"state.json").read_text())
        self.assertEqual(state["jobs"]["style"]["task_id"], "provider-accepted-1")
        self.assertEqual(state["jobs"]["style"]["status"], "succeeded")

if __name__ == "__main__":
    unittest.main()
