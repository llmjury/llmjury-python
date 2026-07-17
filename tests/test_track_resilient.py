"""``track``/``assign`` must never block or throw into the host app, even with a dead network."""

from __future__ import annotations

import time

import pytest
from conftest import FailingTransport, RecordingTransport

from llmjury import Client


def test_track_never_raises_and_drops_on_failure() -> None:
    client = Client("pk_test", transport=FailingTransport(), flush_interval=60.0)
    # A failing transport must not surface as an exception in the caller's path.
    started = time.monotonic()
    for i in range(50):
        client.track("exposure", {"experiment_id": "exp", "user_id": f"u{i}", "variant": "control"})
    # 50 enqueue calls return effectively instantly — no network on the hot path.
    assert time.monotonic() - started < 0.5
    client.flush(timeout=2.0)
    client.close(timeout=5.0)
    # No offline buffer configured, so after exhausted retries the batch is dropped — not raised.
    assert client._buffer.dropped == 50


def test_track_spills_to_offline_when_configured(tmp_path) -> None:
    path = str(tmp_path / "spill.jsonl")
    client = Client("pk_test", transport=FailingTransport(), offline_path=path, flush_interval=60.0)
    client.track("model_call", {"experiment_id": "e", "user_id": "u", "model": "m"})
    client.flush(timeout=2.0)
    client.close(timeout=5.0)
    # With an offline buffer the failed batch is persisted, not dropped.
    assert client._buffer.dropped == 0
    from llmjury import OfflineBuffer

    assert len(OfflineBuffer(path).load_replayable()) == 1


def test_assign_returns_none_and_never_raises_without_config() -> None:
    client = Client("pk_test", transport=FailingTransport())
    assert client.assign("exp", "user") is None
    client.close()


def test_assign_resolves_from_polled_config() -> None:
    config = [
        {
            "id": "exp_x",
            "salt": "s",
            "bucket_count": 1000,
            "version": 3,
            "allocation": [
                {"variant": "control", "weight": 50},
                {"variant": "treatment", "weight": 50},
            ],
        }
    ]
    client = Client("pk_test", transport=RecordingTransport(config))
    assert client.refresh_config("exp_x") is True
    variant = client.assign("exp_x", "user-0")
    assert variant in {"control", "treatment"}
    # Deterministic: same inputs, same variant.
    assert client.assign("exp_x", "user-0") == variant
    client.close()


def test_async_variants_are_thin_and_non_blocking() -> None:
    # The async variants delegate to the sync path (assign is pure compute; track only enqueues),
    # so they can be awaited from any event loop without blocking it. Exercised without an event
    # loop here by driving the coroutine to completion manually.
    client = Client("pk_test", transport=FailingTransport())
    coro = client.aassign("exp", "user")
    with pytest.raises(StopIteration) as stop:
        coro.send(None)
    assert stop.value.value is None
    client.close()
