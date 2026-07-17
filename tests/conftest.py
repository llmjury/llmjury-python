"""Shared test doubles. No real network: every transport here is in-process."""

from __future__ import annotations

from llmjury.client import ConfigResponse, IngestAck, Transport


class RecordingTransport(Transport):
    """Succeeds on every call and records what was sent. Serves an optional fixed config."""

    def __init__(self, configs: list[dict] | None = None) -> None:
        self._configs = configs or []
        self.sent_events: list[dict] = []
        self.event_calls = 0
        self.config_calls = 0

    def get_config(self, experiment_id, etag):
        self.config_calls += 1
        if not self._configs:
            return ConfigResponse(304, [], etag)
        return ConfigResponse(200, self._configs, "etag-1")

    def post_events(self, events):
        self.event_calls += 1
        self.sent_events.extend(events)
        return IngestAck(len(events), 0)


class FailingTransport(Transport):
    """Raises on every call — stands in for a dead network."""

    def get_config(self, experiment_id, etag):
        raise ConnectionError("network down")

    def post_events(self, events):
        raise ConnectionError("network down")
