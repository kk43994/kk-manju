"""Small real-media regressions for export errors seen during production."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "export_verify", Path(__file__).resolve().parents[1] / "scripts" / "export_verify.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class RealExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="kk-export-test-")
        cls.root = Path(cls.temp.name)
        cls.good = cls.root / "good.mkv"
        cls.run_ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1.25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.25",
            "-c:v", "ffv1", "-level", "1", "-c:a", "pcm_s16le", str(cls.good),
        )
        cls.gap = cls.root / "gap.mkv"
        cls.run_ffmpeg(
            "-i", str(cls.good), "-an", "-vf", "setpts=PTS+gte(N\\,12)*0.5/TB",
            "-fps_mode", "passthrough", "-c:v", "ffv1", "-level", "1", str(cls.gap),
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def run_ffmpeg(*args):
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-y", *args],
                       capture_output=True, text=True, check=True)

    def test_real_frames_audio_and_quantized_pts_pass(self):
        report = MODULE.verify(self.good, expected_frames=30, expected_fps="24",
                               expected_duration=1.25, threads=1)
        self.assertEqual(report["technical_status"], "passed", report)
        self.assertEqual(report["decoded_frames"], 30)
        self.assertTrue(report["full_audio_video_decode_passed"])
        self.assertEqual(len(report["audio"]), 1)

    def test_wrong_expected_frames_is_not_hidden_by_duration(self):
        report = MODULE.verify(self.good, expected_frames=31, threads=1)
        self.assertIn("decoded_frame_count_mismatch", report["issues"])

    def test_gap_rejected_for_cfr_but_not_unconditionally_for_vfr(self):
        cfr = MODULE.verify(self.gap, expected_frames=30, expected_fps="24", threads=1)
        self.assertIn("expected_cfr_step_mismatch", cfr["issues"])
        vfr = MODULE.verify(self.gap, expected_frames=30, threads=1)
        self.assertEqual(vfr["technical_status"], "passed", vfr)

    def test_download_identity_checked_from_bytes(self):
        copy = self.root / "download.mkv"
        shutil.copyfile(self.good, copy)
        same = MODULE.verify(copy, match_file=self.good, threads=1)
        self.assertTrue(same["delivery_comparison"]["byte_identical"])
        different = MODULE.verify(copy, match_file=self.gap, threads=1)
        self.assertIn("delivery_byte_mismatch", different["issues"])

    def test_mixed_lossless_concat_cannot_silently_pass(self):
        second = self.root / "level3.mkv"
        mixed = self.root / "mixed.mkv"
        self.run_ffmpeg("-i", str(self.good), "-an", "-c:v", "ffv1", "-level", "3", str(second))
        listing = self.root / "concat.txt"
        listing.write_text("file 'good.mkv'\nfile 'level3.mkv'\nfile 'good.mkv'\n")
        self.run_ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing),
                        "-map", "0:v:0", "-c", "copy", str(mixed))
        report = MODULE.verify(mixed, expected_frames=90, expected_fps="24", threads=1)
        self.assertEqual(report["technical_status"], "failed", report)
        self.assertTrue(set(report["issues"]) & {
            "decoded_frame_count_mismatch", "frame_decode_error", "full_audio_video_decode_failed"
        }, report)


if __name__ == "__main__":
    unittest.main()
