"""End-to-end tests for MediaVerifyBot API.

This script:
  1. Waits for the API to be reachable (assumes docker-compose is already up).
  2. Sends test files to POST /analyze.
  3. Validates response structure.
  4. Prints a summary results table.

Usage:
    # Start services first:
    #   docker-compose up -d
    #   docker-compose run --rm api alembic upgrade head

    python tests/e2e_test.py

Environment variables required:
    API_BASE_URL   — defaults to http://localhost:8000
    API_SECRET_KEY — must match the one used by the running API
"""

from __future__ import annotations

# This is an executable scenario (`python tests/e2e_test.py`), not a pytest
# suite. Its helper functions deliberately accept an httpx client argument.
__test__ = False

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_SECRET = os.environ.get("API_SECRET_KEY", "change-me")
ANALYZE_URL = f"{API_BASE_URL}/analyze"
HEALTH_URL = f"{API_BASE_URL}/health"

# Test user for e2e tests
E2E_USER = {
    "user_id": "999999999",
    "username": "e2e_test_bot",
    "first_name": "E2E",
}

TIMEOUT = 120.0  # video analysis can be slow

# ---------------------------------------------------------------------------
# Sample file definitions
# ---------------------------------------------------------------------------

SAMPLES_DIR = Path("media_samples")


@dataclass
class TestCase:
    name: str
    file_path: Path
    content_type: str
    expected_verdicts: list[str]  # acceptable verdicts for this file
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    latency_ms: int = 0


TEST_CASES: list[TestCase] = [
    # Images
    TestCase(
        name="real_photo",
        file_path=SAMPLES_DIR / "real" / "photo1.jpg",
        content_type="image/jpeg",
        expected_verdicts=["REAL", "UNCERTAIN"],
    ),
    TestCase(
        name="ai_generated_image",
        file_path=SAMPLES_DIR / "fake" / "ai_generated1.jpg",
        content_type="image/jpeg",
        expected_verdicts=["FAKE", "UNCERTAIN"],
    ),
    # Audio
    TestCase(
        name="real_voice",
        file_path=SAMPLES_DIR / "real" / "voice1.wav",
        content_type="audio/wav",
        expected_verdicts=["REAL", "UNCERTAIN"],
    ),
    TestCase(
        name="synthetic_speech",
        file_path=SAMPLES_DIR / "fake" / "tts1.wav",
        content_type="audio/wav",
        expected_verdicts=["FAKE", "UNCERTAIN"],
    ),
    # Video
    TestCase(
        name="real_video",
        file_path=SAMPLES_DIR / "real" / "clip1.mp4",
        content_type="video/mp4",
        expected_verdicts=["REAL", "UNCERTAIN"],
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_api(max_wait: int = 60) -> bool:
    """Poll /health until API responds or timeout."""
    print(f"⏳ Waiting for API at {API_BASE_URL} ...")
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            r = httpx.get(HEALTH_URL, timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                print(
                    f"✅ API ready — status={data.get('status')}, db={data.get('db')}"
                )
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _validate_response(body: dict[str, Any]) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors: list[str] = []
    required_fields = [
        "verdict",
        "confidence",
        "model_used",
        "explanation",
        "media_type",
        "processing_ms",
    ]
    for f in required_fields:
        if f not in body:
            errors.append(f"Missing field: {f}")
    if "verdict" in body and body["verdict"] not in ("REAL", "FAKE", "UNCERTAIN"):
        errors.append(f"Unknown verdict: {body['verdict']}")
    if "confidence" in body:
        c = body["confidence"]
        if not (0.0 <= c <= 1.0):
            errors.append(f"Confidence out of range: {c}")
    return errors


def run_test_case(tc: TestCase, client: httpx.Client) -> None:
    if not tc.file_path.exists():
        tc.error = f"SKIP — file not found: {tc.file_path}"
        return

    file_bytes = tc.file_path.read_bytes()
    start = time.monotonic()
    try:
        response = client.post(
            ANALYZE_URL,
            headers={"x-api-secret": API_SECRET},
            data=E2E_USER,
            files={"file": (tc.file_path.name, file_bytes, tc.content_type)},
            timeout=TIMEOUT,
        )
        tc.latency_ms = int((time.monotonic() - start) * 1000)

        if response.status_code != 200:
            tc.error = f"HTTP {response.status_code}: {response.text[:200]}"
            return

        body = response.json()
        validation_errors = _validate_response(body)
        if validation_errors:
            tc.error = "; ".join(validation_errors)
            return

        tc.result = body

    except httpx.TimeoutException:
        tc.error = f"TIMEOUT after {TIMEOUT}s"
    except Exception as exc:
        tc.error = f"Exception: {exc}"


def print_results(cases: list[TestCase]) -> int:
    """Print Markdown-style results table. Returns number of failures."""
    print("\n" + "=" * 90)
    print("E2E Test Results")
    print("=" * 90)
    header = f"{'Test':<25} {'Verdict':<10} {'Confidence':<12} {'Model':<30} {'Latency':<10} {'Status'}"
    print(header)
    print("-" * 90)

    failures = 0
    for tc in cases:
        if tc.error:
            status = "❌ ERROR" if not tc.error.startswith("SKIP") else "⏭  SKIP"
            print(f"{tc.name:<25} {'—':<10} {'—':<12} {'—':<30} {'—':<10} {status}")
            print(f"   └─ {tc.error}")
            if not tc.error.startswith("SKIP"):
                failures += 1
        else:
            verdict = tc.result.get("verdict", "—")
            confidence = f"{tc.result.get('confidence', 0):.2%}"
            model = tc.result.get("model_used", "—")[:28]
            latency = f"{tc.latency_ms}ms"
            expected_ok = verdict in tc.expected_verdicts
            status = "✅ OK" if expected_ok else "⚠️  UNEXPECTED"
            print(
                f"{tc.name:<25} {verdict:<10} {confidence:<12} {model:<30} {latency:<10} {status}"
            )
            if not expected_ok:
                failures += 1

    print("=" * 90)
    passed = sum(
        1
        for tc in cases
        if not tc.error and tc.result.get("verdict") in tc.expected_verdicts
    )
    skipped = sum(1 for tc in cases if tc.error and tc.error.startswith("SKIP"))
    total = len(cases) - skipped
    print(f"Passed: {passed}/{total}  |  Skipped: {skipped}  |  Failures: {failures}")
    print()
    return failures


# ---------------------------------------------------------------------------
# Test: rate limit (send 429)
# ---------------------------------------------------------------------------


def test_missing_secret(client: httpx.Client) -> bool:
    """POST /analyze without x-api-secret must return 403."""
    r = client.post(
        ANALYZE_URL,
        data=E2E_USER,
        files={"file": ("test.txt", b"x" * 60, "text/plain")},
        timeout=10.0,
    )
    ok = r.status_code == 403
    status = "✅ 403 (correct)" if ok else f"❌ Expected 403, got {r.status_code}"
    print(f"{'no_secret_returns_403':<25} {status}")
    return ok


def test_unsupported_mime(client: httpx.Client) -> bool:
    """POST /analyze with application/zip must return 400."""
    r = client.post(
        ANALYZE_URL,
        headers={"x-api-secret": API_SECRET},
        data=E2E_USER,
        files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
        timeout=10.0,
    )
    ok = r.status_code == 400
    status = "✅ 400 (correct)" if ok else f"❌ Expected 400, got {r.status_code}"
    print(f"{'unsupported_mime':<25} {status}")
    return ok


def test_health(client: httpx.Client) -> bool:
    """GET /health must return 200 with status=ok."""
    r = client.get(HEALTH_URL, timeout=5.0)
    body = r.json() if r.status_code == 200 else {}
    ok = r.status_code == 200 and body.get("status") == "ok"
    status = "✅ OK" if ok else f"❌ {r.status_code} {body}"
    print(f"{'health_endpoint':<25} {status}")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not wait_for_api():
        print("❌ API did not become ready in time. Is docker-compose up?")
        return 1

    print("\n--- Security / Structure checks ---")
    with httpx.Client() as client:
        health_ok = test_health(client)
        sec_ok = test_missing_secret(client)
        mime_ok = test_unsupported_mime(client)

    print("\n--- Media analysis ---")
    with httpx.Client() as client:
        for tc in TEST_CASES:
            run_test_case(tc, client)

    failures = print_results(TEST_CASES)
    if not (health_ok and sec_ok and mime_ok):
        failures += 1

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
