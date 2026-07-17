"""Name-based addressing: unique experiment names resolve like ids, and always hash the id."""

from __future__ import annotations

from conftest import RecordingTransport

from llmjury import Client

CONFIG = [
    {
        "id": "exp_x",
        "name": "checkout-copy",
        "salt": "s",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {"variant": "control", "weight": 50, "prompt": "Control prompt."},
            {"variant": "treatment", "weight": 50},
        ],
    }
]


def test_assign_by_name_matches_assign_by_id() -> None:
    # The frozen bucketing hash must run on the canonical id either way — a name-addressed call
    # that hashed the name would assign users differently from the server.
    client = Client("pk_test", transport=RecordingTransport(CONFIG))
    assert client.refresh_config("checkout-copy") is True
    for user in ("u-1", "u-2", "u-3", "u-4"):
        assert client.assign("checkout-copy", user) == client.assign("exp_x", user)
    client.close()


def test_get_prompt_resolves_by_name() -> None:
    client = Client("pk_test", transport=RecordingTransport(CONFIG))
    assert client.refresh_config("checkout-copy") is True
    result = client.get_prompt("checkout-copy", "u-1", default="fallback")
    assert result.variant in {"control", "treatment"}
    client.close()


def test_tracked_events_normalize_the_name_to_the_canonical_id() -> None:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("checkout-copy") is True
    client.track(
        "exposure",
        {"experiment_id": "checkout-copy", "user_id": "u-1", "variant": "control"},
    )
    client.flush(timeout=2.0)
    client.close()
    assert len(transport.sent_events) == 1
    # The pipeline is keyed by the canonical id — the name must never reach ingest.
    assert transport.sent_events[0]["experiment_id"] == "exp_x"
