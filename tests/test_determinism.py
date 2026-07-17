"""The mandatory cross-SDK determinism test (spec/bucketing.md §5).

Reads the shared fixture and asserts this SDK reproduces ``expected_variant`` for every case. The
TypeScript and Java SDKs assert against the *same* fixture, so a green run
here is half of the cross-language guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmjury import assign_variant, bucket_of

FIXTURE = Path(__file__).resolve().parents[1] / "spec" / "fixtures" / "bucketing-cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def test_fixture_is_present() -> None:
    assert FIXTURE.exists(), f"missing cross-language fixture at {FIXTURE}"
    assert CASES, "fixture has no cases"


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{c['experiment_id']}:{c['user_id']}" for c in CASES],
)
def test_bucketing_matches_fixture(case: dict) -> None:
    if "bucket" in case:
        assert (
            bucket_of(case["salt"], case["user_id"], case["experiment_id"], case["bucket_count"])
            == case["bucket"]
        )
    assert (
        assign_variant(
            case["salt"],
            case["user_id"],
            case["experiment_id"],
            case["bucket_count"],
            case["allocation"],
        )
        == case["expected_variant"]
    )
