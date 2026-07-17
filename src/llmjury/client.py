"""The LLMJury client: deterministic ``assign`` + non-blocking ``track``.

Design:

* ``assign(experiment, user)`` is **pure local compute** against the polled experiment config — no
  network on the hot path, so it never blocks. It returns the variant *key* (or ``None`` while the
  config is still being fetched), reproducing the frozen bucketing hash exactly.
* ``track(event, payload)`` only appends to an in-memory buffer that flushes asynchronously; it
  never blocks or throws into the caller.
* Experiment config is fetched by **polling** ``GET /v1/config`` with ``If-None-Match``/``304`` into
  an in-process cache (default every 60s). When a ``track`` (ingest) response carries a newer
  ``config_version`` than what is cached, an immediate refresh is triggered. No SSE/WebSocket.
* Authentication is the org **publishable** key via the ``X-API-Key`` header.

The transport is injectable (the default uses only the Python standard library — zero runtime
dependencies); tests pass a fake transport to exercise failure paths without a network.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from .bucketing import AllocationSlice, assign_variant
from .buffer import EventBuffer
from .intercept import ModelCall
from .offline import OfflineBuffer
from .wrap import WrappedClient

#: The ambient user for wrapped provider clients — set per request via ``Client.as_user``.
_CURRENT_USER: ContextVar[str | None] = ContextVar("llmjury_user", default=None)

_DEFAULT_BUCKET_COUNT = 1000

#: Production API base URL. Override with ``base_url`` or the ``LLMJURY_BASE_URL`` env var.
_DEFAULT_BASE_URL = "https://api.llmjury.com"


@dataclass
class CachedConfig:
    """The bucketing inputs the SDK needs, distilled from a fetched ``ExperimentConfig``."""

    experiment_id: str
    salt: str
    bucket_count: int
    allocation: list[AllocationSlice]
    version: int
    prompts: dict[str, str] = field(default_factory=dict)  # variant -> prompt text (if set)
    variables: dict[str, dict[str, str]] = field(default_factory=dict)  # variant -> vars
    name: str | None = None  # unique, human-readable experiment name (an alternate address)


@dataclass
class VariantVariables:
    """Result of :meth:`Client.get_variables` — always usable, never raises.

    ``values`` starts from the caller's in-code ``defaults`` and overlays the assigned variant's
    configured variables (plus its ``prompt``, when set, under the ``"prompt"`` key). Everything is
    resolved from the client-memory config cache — no API call on this path; the cache refreshes in
    the background (60s poll) and immediately when an event send reports a newer config version.
    ``fallback`` is ``True`` when the defaults were used unmodified (config unreachable).
    """

    variant: str | None
    values: dict[str, str]
    fallback: bool


@dataclass
class PromptAssignment:
    """Result of :meth:`Client.get_prompt` — always usable, never raises.

    ``variant`` is the assigned variant key, or ``None`` when assignment could not resolve (config
    not yet cached / backend unreachable). ``prompt`` is the text to use: the variant's configured
    prompt when available, otherwise the caller's in-code default. ``fallback`` is ``True`` whenever
    the returned prompt is the default rather than the variant's configured prompt.
    """

    variant: str | None
    prompt: str
    fallback: bool


@dataclass
class ConfigResponse:
    """Result of a ``GET /v1/config`` poll. ``status`` is 200 (configs) or 304 (unchanged)."""

    status: int
    configs: list[dict] = field(default_factory=list)
    etag: str | None = None


@dataclass
class IngestAck:
    """Result of ``POST /v1/events``. ``config_version`` is the org-aggregate staleness hint."""

    accepted: int
    config_version: int


class Transport:
    """Boundary the client talks to. Swap a fake in for tests; default is :class:`HttpTransport`."""

    def get_config(self, experiment_id: str | None, etag: str | None) -> ConfigResponse:
        raise NotImplementedError

    def post_events(self, events: list[dict]) -> IngestAck:
        raise NotImplementedError


class HttpTransport(Transport):
    """Standard-library (urllib) HTTP transport — no third-party runtime dependency."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def get_config(self, experiment_id: str | None, etag: str | None) -> ConfigResponse:
        url = self._base + "/v1/config"
        if experiment_id:
            url += "?experiment_id=" + urllib.parse.quote(experiment_id, safe="")
        request = urllib.request.Request(url, method="GET")
        request.add_header("X-API-Key", self._api_key)
        if etag:
            request.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
                configs = json.loads(body) if body else []
                return ConfigResponse(200, configs, response.headers.get("ETag"))
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return ConfigResponse(304, [], etag)
            raise

    def post_events(self, events: list[dict]) -> IngestAck:
        url = self._base + "/v1/events"
        data = json.dumps({"events": events}).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("X-API-Key", self._api_key)
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            body = response.read()
            ack = json.loads(body) if body else {}
        return IngestAck(int(ack.get("accepted", 0)), int(ack.get("config_version", 0)))


class Client:
    """Public SDK entrypoint. Construct once per process and reuse; call :meth:`close` at exit."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        experiments: Sequence[str] | None = None,
        transport: Transport | None = None,
        flush_interval: float = 1.0,
        flush_size: int = 1000,
        config_poll_interval: float = 60.0,
        offline_path: str | None = None,
        request_timeout: float = 5.0,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Construct a client.

        ``api_key`` is the org **publishable** key; it falls back to the ``LLMJURY_API_KEY`` env
        var, so ``Client()`` with no arguments works when that is set. ``base_url`` falls back to
        ``LLMJURY_BASE_URL`` and then the production host. Pass ``experiments`` to prefetch configs
        at startup so the first :meth:`assign` resolves immediately.
        """
        self._log = logger or logging.getLogger("llmjury")
        if transport is None:
            key = api_key or os.environ.get("LLMJURY_API_KEY")
            if not key:
                raise ValueError(
                    "llmjury: missing API key. Pass api_key or set the LLMJURY_API_KEY "
                    "environment variable."
                )
            base = base_url or os.environ.get("LLMJURY_BASE_URL") or _DEFAULT_BASE_URL
            transport = HttpTransport(base, key, request_timeout)
        self._transport = transport
        self._poll_interval = config_poll_interval
        self._clock = clock

        self._configs: dict[str, CachedConfig] = {}
        self._etags: dict[str, str | None] = {}
        self._highest_version = 0
        self._cfg_lock = threading.Lock()

        offline = OfflineBuffer(offline_path, logger=self._log) if offline_path else None
        self._buffer = EventBuffer(
            self._send_events,
            flush_interval=flush_interval,
            flush_size=flush_size,
            offline=offline,
            logger=self._log,
            clock=clock,
        )

        self._pending: set = set()
        self._poll_wake = threading.Event()
        self._stop = threading.Event()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="llmjury-config", daemon=True
        )
        self._poll_thread.start()

        # Prefetch declared experiments so the first assign resolves without a manual warm-up call.
        for experiment_id in experiments or ():
            self._request_refresh(experiment_id)

    # -- public API ---------------------------------------------------------------------------

    def assign(self, experiment: str, user: str) -> str | None:
        """Resolve ``user`` to a variant key for ``experiment`` (deterministic, frozen hash).

        ``experiment`` is the experiment id OR its unique name — names are resolved through the
        polled config, and the frozen bucketing hash always runs on the canonical id, so name- and
        id-addressed calls assign identically.

        Pure local compute against the cached config — never blocks, never raises. Returns ``None``
        if the config is not yet cached; the SDK fetches it in the background, so a later call
        resolves. Callers should treat ``None`` as "fall back to your control behaviour".
        """
        try:
            cached = self._get_cached(experiment)
            if cached is None:
                self._request_refresh(experiment)
                return None
            # ALWAYS hash the canonical id — hashing a name would diverge from server assignment.
            return assign_variant(
                cached.salt, user, cached.experiment_id, cached.bucket_count, cached.allocation
            )
        except Exception as exc:  # belt and braces — assign must never throw into the host app.
            self._log.warning("llmjury: assign failed for %s/%s: %s", experiment, user, exc)
            return None

    def get_prompt(self, experiment: str, user: str, default: str) -> PromptAssignment:
        """Resolve ``user`` straight to the prompt text for their assigned variant.

        ``default`` is the **in-code fallback prompt** — it is returned whenever LLMJury cannot be
        reached (config not yet cached), assignment fails, or the assigned variant has no prompt
        configured. This is the recommended integration: the app always has a working prompt even
        during a full LLMJury outage. Never blocks, never raises.

        Track the exposure with the returned ``variant`` when it is not ``None``; on ``variant is
        None`` the user saw the default path and no exposure should be recorded.
        """
        try:
            variant = self.assign(experiment, user)
            if variant is None:
                return PromptAssignment(None, default, True)
            cached = self._get_cached(experiment)
            prompt = cached.prompts.get(variant) if cached is not None else None
            if prompt is None or prompt == "":
                return PromptAssignment(variant, default, True)
            return PromptAssignment(variant, prompt, False)
        except Exception as exc:  # must never throw into the host app.
            self._log.warning("llmjury: get_prompt failed for %s/%s: %s", experiment, user, exc)
            return PromptAssignment(None, default, True)

    def wrap(self, provider_client: object, experiment: str) -> WrappedClient:
        """Setup-once interception (OkHttp style): wrap the provider client and call it directly.

        ::

            llm = client.wrap(openai_client, experiment="checkout-copy")   # once, at startup

            with client.as_user(user_id):                                  # once per request
                response = llm.chat.completions.create(model=m, messages=[...])

        Every model-shaped call through the wrapper records the exposure + a ``model_call`` with
        measured latency, tokens, model, and errors — no call-site code. Without an ambient user
        the call passes through untouched.
        """
        return WrappedClient(provider_client, self, experiment)

    def as_user(self, user: str):
        """Bind the ambient user for wrapped clients within a ``with`` block (async-safe)."""

        class _UserScope:
            def __enter__(self) -> str:
                self._token = _CURRENT_USER.set(user)
                return user

            def __exit__(self, *_exc: object) -> None:
                _CURRENT_USER.reset(self._token)

        return _UserScope()

    def set_user(self, user: str | None) -> None:
        """Set the ambient user without a scope (scripts; prefer :meth:`as_user` in servers)."""
        _CURRENT_USER.set(user)

    def current_user(self) -> str | None:
        """The ambient user bound by :meth:`as_user`/:meth:`set_user`, or ``None``."""
        return _CURRENT_USER.get()

    def get_variables(
        self, experiment: str, user: str, defaults: dict | None = None
    ) -> VariantVariables:
        """Resolve ``user`` to their variant's custom variables, merged over in-code ``defaults``.

        Pure client-memory read (the polled config cache) — never a request-path API call. The
        variant's ``prompt``, when configured, is included under the ``"prompt"`` key. On any
        failure path the defaults come back unchanged with ``fallback=True``. Never raises.
        """
        merged: dict = dict(defaults or {})
        try:
            variant = self.assign(experiment, user)
            if variant is None:
                return VariantVariables(None, merged, True)
            cached = self._get_cached(experiment)
            configured = cached.variables.get(variant, {}) if cached else {}
            prompt = cached.prompts.get(variant) if cached else None
            if prompt:
                merged["prompt"] = prompt
            merged.update(configured)
            return VariantVariables(variant, merged, not configured and not prompt)
        except Exception as exc:  # must never throw into the host app.
            self._log.warning("llmjury: get_variables failed for %s/%s: %s", experiment, user, exc)
            return VariantVariables(None, merged, True)

    def intercept_model_call(
        self, experiment: str, user: str, variant: str | None = None
    ) -> ModelCall:
        """Wrap an LLM call so its implicit metrics are captured with NO explicit tracking.

        Use as a context manager around the model call::

            a = client.get_prompt("checkout-copy", user_id, default=DEFAULT_PROMPT)
            with client.intercept_model_call("checkout-copy", user_id, a.variant) as call:
                response = llm(a.prompt, user_input)
                call.record(response, prompt=a.prompt)

        On exit the SDK tracks the exposure and a ``model_call`` event with the measured
        ``latency_ms``, ``ttft_ms`` (when :meth:`ModelCall.mark_first_token` was called), and the
        model/token fields extracted from the response. An exception inside the block is recorded
        as ``metadata.error = True`` and re-raised. When ``variant`` is ``None`` (the getPrompt
        fallback path) nothing is recorded. Only business outcomes still need :meth:`track`.

        ``variant`` defaults to :meth:`assign` when omitted.
        """
        resolved = variant if variant is not None else self.assign(experiment, user)
        return ModelCall(self.track, experiment, user, resolved)

    def track(self, event: str, payload: dict) -> None:
        """Buffer an event of type ``event`` (``exposure`` | ``model_call`` | ``business_event``).

        ``payload`` carries the event fields (``experiment_id``, ``user_id`` and any metrics). The
        SDK stamps a client-generated ``event_id`` and ``timestamp`` if absent. Non-blocking; never
        raises into the caller.
        """
        try:
            record = self._build_event(event, payload)
            if record is None:
                return
            self._buffer.add(record)
        except Exception as exc:  # never propagate.
            self._log.warning("llmjury: track failed for %s: %s", event, exc)

    async def aassign(self, experiment: str, user: str) -> str | None:
        """Async variant of :meth:`assign`. Returns immediately — assign is pure local compute."""
        return self.assign(experiment, user)

    async def atrack(self, event: str, payload: dict) -> None:
        """Async variant of :meth:`track`. Returns immediately — track only enqueues."""
        self.track(event, payload)

    def refresh_config(self, experiment_id: str, timeout: float = 5.0) -> bool:
        """Blocking, exception-safe config fetch. Useful at startup so the first ``assign``
        resolves; not part of the hot path. Returns ``True`` if the config is cached afterwards."""
        del timeout  # the transport carries its own request timeout.
        self._fetch_config(experiment_id)
        return self._get_cached(experiment_id) is not None

    def flush(self, timeout: float = 5.0) -> None:
        """Best-effort blocking flush of buffered events (e.g. before a short script exits)."""
        self._buffer.flush(timeout)

    def close(self, timeout: float = 5.0) -> None:
        """Flush and stop background threads. Call at process shutdown."""
        self._stop.set()
        self._poll_wake.set()
        self._poll_thread.join(timeout=timeout)
        self._buffer.close(timeout)

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------------------------

    def _build_event(self, event: str, payload: dict) -> dict | None:
        record = dict(payload)
        record["type"] = event
        record.setdefault("event_id", str(uuid.uuid4()))
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        if not record.get("experiment_id") or not record.get("user_id"):
            self._log.warning("llmjury: dropping %s event missing experiment_id/user_id", event)
            return None
        # Name-addressed events normalize to the canonical id so the analysis pipeline (keyed by
        # id) attributes them; unknown identifiers pass through untouched. The held config version
        # is stamped so the version-scoped rollups attribute the event to the config it ran under.
        cached = self._get_cached(str(record["experiment_id"]))
        if cached is not None:
            record["experiment_id"] = cached.experiment_id
            record.setdefault("config_version", cached.version)
        return record

    def _send_events(self, batch: list[dict]) -> None:
        ack = self._transport.post_events(batch)
        if ack.config_version > self._highest_version:
            # Staleness hint: org config advanced past what we hold — refresh everything we know.
            with self._cfg_lock:
                known = list(self._configs.keys())
            for experiment_id in known:
                self._request_refresh(experiment_id)

    def _get_cached(self, experiment_id: str) -> CachedConfig | None:
        with self._cfg_lock:
            return self._configs.get(experiment_id)

    def _request_refresh(self, experiment_id: str) -> None:
        with self._cfg_lock:
            self._pending.add(experiment_id)
        self._poll_wake.set()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            woke = self._poll_wake.wait(self._poll_interval)
            self._poll_wake.clear()
            with self._cfg_lock:
                pending = self._pending
                self._pending = set()
                targets = set(pending)
                if not woke:  # periodic tick — re-poll everything we already track.
                    targets.update(self._configs.keys())
            for experiment_id in targets:
                if self._stop.is_set():
                    return
                self._fetch_config(experiment_id)

    def _fetch_config(self, experiment_id: str) -> None:
        try:
            with self._cfg_lock:
                etag = self._etags.get(experiment_id)
            response = self._transport.get_config(experiment_id, etag)
            if response.status == 304:
                return
            self._store_configs(response, experiment_id)
        except Exception as exc:  # keep the last-known config on any fetch error.
            self._log.warning("llmjury: config fetch failed for %s: %s", experiment_id, exc)

    def _store_configs(self, response: ConfigResponse, requested_id: str) -> None:
        with self._cfg_lock:
            for raw in response.configs:
                cached = self._parse_config(raw)
                if cached is None:
                    continue
                self._configs[cached.experiment_id] = cached
                if cached.name:  # names are unique per org — index the config under both addresses
                    self._configs[cached.name] = cached
                self._highest_version = max(self._highest_version, cached.version)
            if response.etag is not None:
                self._etags[requested_id] = response.etag

    @staticmethod
    def _parse_config(raw: dict) -> CachedConfig | None:
        experiment_id = raw.get("id") or raw.get("experiment_id")
        if not experiment_id or "salt" not in raw or "allocation" not in raw:
            return None
        allocation = [
            {"variant": slice_["variant"], "weight": int(slice_["weight"])}
            for slice_ in raw["allocation"]
        ]
        prompts = {
            slice_["variant"]: slice_["prompt"]
            for slice_ in raw["allocation"]
            if isinstance(slice_.get("prompt"), str) and slice_["prompt"]
        }
        variables = {
            slice_["variant"]: {str(k): str(v) for k, v in slice_["variables"].items()}
            for slice_ in raw["allocation"]
            if isinstance(slice_.get("variables"), dict) and slice_["variables"]
        }
        name = raw.get("name")
        return CachedConfig(
            experiment_id=experiment_id,
            salt=raw["salt"],
            bucket_count=int(raw.get("bucket_count", _DEFAULT_BUCKET_COUNT)),
            allocation=allocation,
            version=int(raw.get("version", 0)),
            prompts=prompts,
            variables=variables,
            name=name if isinstance(name, str) and name else None,
        )
