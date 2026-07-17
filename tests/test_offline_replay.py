"""Offline spill + replay: events survive an outage and replay with their original timestamps,
bounded to a 24h age."""

from __future__ import annotations

from pathlib import Path

from conftest import FailingTransport, RecordingTransport

from llmjury import Client, OfflineBuffer


def test_load_replayable_respects_24h_bound(tmp_path) -> None:
    clock = {"now": 1_000_000.0}
    buf = OfflineBuffer(str(tmp_path / "spill.jsonl"), now=lambda: clock["now"])

    buf.append_all([{"event_id": "old", "type": "exposure", "timestamp": "T-old"}])
    clock["now"] += 25 * 3600  # 25h later — the old record is now past the bound
    buf.append_all([{"event_id": "new", "type": "exposure", "timestamp": "T-new"}])
    clock["now"] += 1

    replay = buf.load_replayable()
    assert [e["event_id"] for e in replay] == ["new"]
    # Original timestamp is preserved verbatim — replay is faithful.
    assert replay[0]["timestamp"] == "T-new"


def test_events_spilled_offline_replay_on_next_client(tmp_path) -> None:
    path = str(tmp_path / "spill.jsonl")

    # Client 1 cannot reach the network, so the event spills to the offline file.
    c1 = Client("pk", transport=FailingTransport(), offline_path=path, flush_interval=60.0)
    c1.track("exposure", {"experiment_id": "e", "user_id": "u", "variant": "control"})
    c1.flush(timeout=2.0)
    c1.close(timeout=5.0)
    assert Path(path).exists()

    # Client 2 has a working transport: on startup it replays the spilled event and clears the file.
    working = RecordingTransport()
    c2 = Client("pk", transport=working, offline_path=path, flush_interval=60.0)
    c2.flush(timeout=2.0)
    c2.close(timeout=5.0)

    assert len(working.sent_events) == 1
    assert working.sent_events[0]["type"] == "exposure"
    assert working.sent_events[0]["user_id"] == "u"
    assert not Path(path).exists()  # cleared after a successful replay
