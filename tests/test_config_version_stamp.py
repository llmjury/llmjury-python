"""Tracked events carry the held config version, so version-scoped rollups attribute them."""

from __future__ import annotations

from conftest import RecordingTransport

from llmjury import Client

CONFIG = [
    {
        "id": "exp_v",
        "name": "versioned-exp",
        "salt": "s",
        "bucket_count": 1000,
        "version": 3,
        "allocation": [
            {"variant": "control", "weight": 50},
            {"variant": "treatment", "weight": 50},
        ],
    }
]


def test_tracked_events_are_stamped_with_the_held_config_version() -> None:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("exp_v") is True

    client.track("exposure", {"experiment_id": "exp_v", "user_id": "u-1", "variant": "control"})
    client.flush()
    client.close()

    assert transport.sent_events, "the exposure must have been sent"
    assert transport.sent_events[0]["config_version"] == 3


def test_an_explicit_config_version_is_never_overwritten() -> None:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("exp_v") is True

    client.track(
        "exposure",
        {"experiment_id": "exp_v", "user_id": "u-1", "variant": "control", "config_version": 2},
    )
    client.flush()
    client.close()

    assert transport.sent_events[0]["config_version"] == 2


def test_events_for_an_uncached_experiment_carry_no_fabricated_version() -> None:
    transport = RecordingTransport([])  # nothing cached
    client = Client("pk_test", transport=transport)

    client.track("exposure", {"experiment_id": "exp_unknown", "user_id": "u-1", "variant": "c"})
    client.flush()
    client.close()

    assert "config_version" not in transport.sent_events[0]
