"""Durable offline spill buffer for telemetry events.

When the in-memory :class:`~llmjury.buffer.EventBuffer` exhausts its network retries it spills the
batch here instead of dropping it, so events survive a process restart or an extended outage. On the
next client start the events are replayed with their **original** timestamps. Replay is bounded to a
24h age — anything older is discarded so a stale file can
never flood the backend with ancient data.

The file is newline-delimited JSON; each line is ``{"event": <event>, "saved_at": <epoch>}``.
``saved_at`` is wall-clock at spill time and is what the 24h bound is measured against; the
event's own ``timestamp`` field is left untouched so the replay is faithful.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable

_DAY_SECONDS = 24 * 60 * 60


class OfflineBuffer:
    """A file-backed spill+replay buffer. All operations are process-safe via an internal lock and
    never raise into the caller — I/O failures are logged and swallowed (an SDK must never throw
    into the host app)."""

    def __init__(
        self,
        path: str,
        *,
        max_age_seconds: int = _DAY_SECONDS,
        now: Callable[[], float] = time.time,
        logger: logging.Logger | None = None,
    ) -> None:
        self._path = path
        self._max_age = max_age_seconds
        self._now = now
        self._log = logger or logging.getLogger("llmjury")
        self._lock = threading.Lock()

    def append_all(self, events: list[dict]) -> None:
        """Append events to the spill file, stamping each with the current wall-clock time."""
        if not events:
            return
        saved_at = self._now()
        with self._lock:
            try:
                directory = os.path.dirname(self._path)
                if directory:
                    os.makedirs(directory, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as handle:
                    for event in events:
                        handle.write(json.dumps({"event": event, "saved_at": saved_at}))
                        handle.write("\n")
            except OSError as exc:  # disk full / permission — last resort, just log.
                self._log.warning(
                    "llmjury: failed to persist %d offline events: %s", len(events), exc
                )

    def load_replayable(self) -> list[dict]:
        """Return events younger than the 24h bound, in original order, with original timestamps.

        Older records are silently dropped. Returns an empty list if there is nothing to replay or
        the file is unreadable.
        """
        cutoff = self._now() - self._max_age
        with self._lock:
            try:
                with open(self._path, encoding="utf-8") as handle:
                    lines = handle.readlines()
            except FileNotFoundError:
                return []
            except OSError as exc:
                self._log.warning("llmjury: failed to read offline buffer: %s", exc)
                return []
        events: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # tolerate a torn final line from a crash mid-write.
            if float(record.get("saved_at", 0)) >= cutoff:
                events.append(record["event"])
        return events

    def clear(self) -> None:
        """Remove the spill file (called after a successful replay)."""
        with self._lock:
            try:
                os.remove(self._path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._log.warning("llmjury: failed to clear offline buffer: %s", exc)
