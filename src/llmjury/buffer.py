"""In-memory, background-flushed event buffer.

``track`` must never block or throw into the host app's call path. So the public
side of this buffer is a non-blocking :meth:`EventBuffer.add` that only appends to a deque; all
network I/O happens on a daemon thread that flushes on an interval (default 1s) or when the buffer
reaches a size threshold (default 1000). A flush retries a bounded number of times and then either
spills to the :class:`~llmjury.offline.OfflineBuffer` (if configured) or drops the batch with a log
line — it never propagates an exception out.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable, Dict, List

from .offline import OfflineBuffer

# A sender takes a batch and returns nothing; it raises on a failed send so the buffer can retry.
Sender = Callable[[List[Dict]], None]


class EventBuffer:
    """Bounded in-memory buffer with a background flusher. Construction starts the flush thread;
    call :meth:`close` to flush and stop it."""

    def __init__(
        self,
        sender: Sender,
        *,
        flush_interval: float = 1.0,
        flush_size: int = 1000,
        max_buffered: int = 100_000,
        max_retries: int = 3,
        retry_backoff: float = 0.2,
        offline: OfflineBuffer | None = None,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sender = sender
        self._flush_interval = flush_interval
        self._flush_size = flush_size
        self._max_buffered = max_buffered
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._offline = offline
        self._log = logger or logging.getLogger("llmjury")
        self._clock = clock
        self._sleep = sleep

        self._queue: collections.deque[dict] = collections.deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dropped = 0

        # Replay anything spilled by a previous run before we start accepting new events.
        if self._offline is not None:
            for event in self._offline.load_replayable():
                self._queue.append(event)
            self._offline.clear()

        self._thread = threading.Thread(target=self._run, name="llmjury-flush", daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        """Total events dropped (buffer overflow or exhausted retries with no offline buffer)."""
        return self._dropped

    def add(self, event: dict) -> None:
        """Enqueue an event. Non-blocking; never raises. Drops the oldest event on overflow."""
        with self._lock:
            if len(self._queue) >= self._max_buffered:
                self._queue.popleft()
                self._dropped += 1
                self._log.warning(
                    "llmjury: event buffer full (%d); dropping oldest", self._max_buffered
                )
            self._queue.append(event)
            due = len(self._queue) >= self._flush_size
        if due:
            self._wake.set()

    def flush(self, timeout: float = 5.0) -> None:
        """Signal a flush and block (best-effort) until the buffer drains or ``timeout`` elapses."""
        self._wake.set()
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            with self._lock:
                empty = not self._queue
            if empty:
                return
            self._sleep(0.01)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the flusher after a final drain. Safe to call more than once."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._flush_interval)
            self._wake.clear()
            self._flush_once()
        self._flush_once()  # final drain on shutdown

    def _flush_once(self) -> None:
        while True:
            batch = self._take_batch()
            if not batch:
                return
            self._send_with_retry(batch)

    def _take_batch(self) -> list[dict]:
        with self._lock:
            if not self._queue:
                return []
            count = min(self._flush_size, len(self._queue))
            return [self._queue.popleft() for _ in range(count)]

    def _send_with_retry(self, batch: list[dict]) -> None:
        for attempt in range(self._max_retries):
            try:
                self._sender(batch)
                return
            except Exception as exc:  # any transport error — retry, then spill/drop.
                if attempt + 1 < self._max_retries:
                    self._sleep(self._retry_backoff * (attempt + 1))
                    continue
                self._log.warning(
                    "llmjury: flush of %d events failed after %d attempts: %s",
                    len(batch),
                    self._max_retries,
                    exc,
                )
        if self._offline is not None:
            self._offline.append_all(batch)
        else:
            self._dropped += len(batch)
