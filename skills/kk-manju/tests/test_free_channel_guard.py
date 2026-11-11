import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from free_channel_guard import fingerprint, verify


def test_quote_is_bound_to_exact_free_request(tmp_path):
    proof = tmp_path / "quote.png"
    proof.write_bytes(b"visible quote evidence")
    request = {"project_id": "project", "id": "V01", "kind": "video",
               "request_key": "stable-key", "prompt": "complete prompt",
               "parameters": {"model": "star-video2-fast", "resolution": "480p",
                              "duration": 12, "count": 1},
               "references": [{"role": "identity", "node_id": "i-one"}]}
    quote = {"project_id": "project", "account_id": 1,
             "request_key": "stable-key", "request_fingerprint": fingerprint(request),
             "credits_total": 0, "channel_label": "Seedance 2.0 Fast VIP",
             "quoted_at_utc": "2026-11-09T22:00:00Z",
             "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
    at = datetime(2026, 11, 9, 22, 5, tzinfo=timezone.utc)
    assert verify(request, quote, proof, at) == []
    request["references"][0]["node_id"] = "i-other"
    assert "frozen request fingerprint mismatch" in verify(request, quote, proof, at)
    request["references"][0]["node_id"] = "i-one"
    quote["credits_total"] = 1
    assert "quote is not exactly zero credits" in verify(request, quote, proof, at)
    quote["credits_total"] = 0
    request["parameters"]["count"] = 2
    assert "zero-credit VIP requires exactly one output per shot" in verify(request, quote, proof, at)
    request["parameters"]["count"] = 1
    assert "quote is stale or from the future" in verify(
        request, quote, proof, datetime(2026, 11, 9, 22, 11, tzinfo=timezone.utc))


def test_explicit_720p_still_requires_its_own_zero_quote(tmp_path):
    proof = tmp_path / "720p.png"
    proof.write_bytes(b"current 720p VIP quote screenshot")
    request = {"project_id": "project", "kind": "video", "request_key": "720-key",
               "prompt": "complete prompt", "parameters": {
                   "model": "star-video2-fast", "resolution": "720p",
                   "duration": 12, "count": 1}, "references": []}
    quote = {"project_id": "project", "account_id": 1, "request_key": "720-key",
             "request_fingerprint": fingerprint(request), "credits_total": 0,
             "channel_label": "Seedance 2.0 Fast VIP", "resolution": "720p",
             "quoted_at_utc": "2026-11-10T08:00:00Z",
             "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
    now = datetime(2026, 11, 10, 8, 1, tzinfo=timezone.utc)
    assert verify(request, quote, proof, now, expected_resolution="720p") == []
    assert "resolution does not match the project selection" in verify(
        request, quote, proof, now)
    quote["resolution"] = "480p"
    assert "quoted resolution does not match the project selection" in verify(
        request, quote, proof, now, expected_resolution="720p")
    quote["resolution"] = "720p"
    quote["credits_total"] = 264
    assert "quote is not exactly zero credits" in verify(
        request, quote, proof, now, expected_resolution="720p")
    quote["credits_total"] = 0
    request["parameters"]["duration"] = 13
    assert "frozen request fingerprint mismatch" in verify(
        request, quote, proof, now, expected_resolution="720p")
