"""A self-contained in-process transport so every example runs with no account and no network.

Real apps never need this file: drop the ``transport=`` argument, set ``LLMJURY_API_KEY``, and the
client talks to https://api.llmjury.com. The config dicts below have exactly the shape the real
``GET /v1/config`` endpoint returns, so everything the examples demonstrate carries over 1:1.
"""

from __future__ import annotations

from llmjury.client import ConfigResponse, IngestAck, Transport


class FakeBackend(Transport):
    """Serves a fixed experiment config and prints what would be ingested."""

    def __init__(self, configs: list[dict]) -> None:
        self._configs = configs

    def get_config(self, experiment_id, etag):
        return ConfigResponse(200, self._configs, "etag-1")

    def post_events(self, events):
        for event in events:
            summary = {k: event.get(k) for k in ("event", "experiment_id", "variant") if k in event}
            print(f"  [ingest] {summary}")
        return IngestAck(len(events), 0)
