"""Model-call interception: the implicit metrics captured without explicit ``track`` calls.

Wrap the LLM call in :meth:`~llmjury.Client.intercept_model_call` and the SDK records the
**exposure** and the **model_call** event automatically, with:

* ``latency_ms`` — wall-clock around the wrapped block (always measured)
* ``ttft_ms`` — time to first token, when the caller marks it (streaming)
* ``model`` / ``tokens_input`` / ``tokens_output`` — duck-typed from OpenAI- and Anthropic-shaped
  response objects (no provider SDK dependency), overridable explicitly
* ``metadata.error`` — ``True`` when the wrapped block raises (the exception is re-raised, never
  swallowed), so an ``error_rate`` metric needs no code at all

The ONLY events an application must still track explicitly are business outcomes
(``business_event``) — everything a model call can tell us is intercepted here, and judge metrics
(quality/safety/relevance) are computed server-side from the recorded prompt/response.
"""

from __future__ import annotations

import time
from typing import Any, Callable

__all__ = ["ModelCall", "extract_response_fields"]


def extract_response_fields(response: Any) -> dict:
    """Best-effort field extraction from a provider response object (duck-typed, never raises).

    Understands OpenAI-shaped (``usage.prompt_tokens``/``completion_tokens``,
    ``choices[0].message.content``) and Anthropic-shaped (``usage.input_tokens``/``output_tokens``,
    ``content[0].text``) responses, plus plain strings. Unknown shapes yield an empty dict — the
    caller can always pass fields explicitly.
    """
    fields: dict = {}
    try:
        if isinstance(response, str):
            return {"response": response}
        model = getattr(response, "model", None)
        if isinstance(model, str) and model:
            fields["model"] = model
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_in = getattr(usage, "input_tokens", None)  # Anthropic
            if tokens_in is None:
                tokens_in = getattr(usage, "prompt_tokens", None)  # OpenAI
            tokens_out = getattr(usage, "output_tokens", None)  # Anthropic
            if tokens_out is None:
                tokens_out = getattr(usage, "completion_tokens", None)  # OpenAI
            if isinstance(tokens_in, int):
                fields["tokens_input"] = tokens_in
            if isinstance(tokens_out, int):
                fields["tokens_output"] = tokens_out
        # Response text: Anthropic content[0].text, else OpenAI choices[0].message.content.
        content = getattr(response, "content", None)
        if isinstance(content, list) and content:
            text = getattr(content[0], "text", None)
            if isinstance(text, str):
                fields["response"] = text
        if "response" not in fields:
            choices = getattr(response, "choices", None)
            if isinstance(choices, list) and choices:
                message = getattr(choices[0], "message", None)
                text = getattr(message, "content", None)
                if isinstance(text, str):
                    fields["response"] = text
    except Exception:  # extraction is best-effort — never break the host app over telemetry.
        pass
    return fields


class ModelCall:
    """Handle for one intercepted model call (returned by ``Client.intercept_model_call``).

    Use as a context manager. On exit it tracks the exposure (once) and the model_call event with
    the measured timings and whatever :meth:`record` captured. An exception inside the block is
    recorded as ``metadata.error = True`` and re-raised — interception never changes control flow.
    """

    def __init__(
        self,
        track: Callable[[str, dict], None],
        experiment: str,
        user: str,
        variant: str | None,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._track = track
        self._experiment = experiment
        self._user = user
        self._variant = variant
        self._clock = clock
        self._started = 0.0
        self._ttft_ms: int | None = None
        self._fields: dict = {}
        self._metadata: dict = {}

    # -- caller surface -------------------------------------------------------------------------

    def mark_first_token(self) -> None:
        """Record time-to-first-token (first call wins) — call when the first stream chunk lands."""
        if self._ttft_ms is None:
            self._ttft_ms = int((self._clock() - self._started) * 1000)

    def record(self, response: Any = None, **fields: Any) -> None:
        """Attach the model response and/or explicit fields to the call.

        ``response`` is duck-type-extracted (model, tokens, text); explicit keyword fields
        (``prompt``, ``model``, ``tokens_input``, ``tokens_output``, ``cost_usd``, ``response``,
        ``metadata``) always win over extracted ones.
        """
        if response is not None:
            self._fields.update(extract_response_fields(response))
        metadata = fields.pop("metadata", None)
        if isinstance(metadata, dict):
            self._metadata.update(metadata)
        self._fields.update({k: v for k, v in fields.items() if v is not None})

    # -- context manager ------------------------------------------------------------------------

    def __enter__(self) -> ModelCall:
        self._started = self._clock()
        return self

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> bool:
        if self._variant is None:
            return False  # fallback path (config unavailable): record nothing, change nothing
        latency_ms = int((self._clock() - self._started) * 1000)
        base = {
            "experiment_id": self._experiment,
            "user_id": self._user,
            "variant": self._variant,
        }
        self._track("exposure", dict(base))
        event: dict = {**base, **self._fields, "latency_ms": latency_ms}
        if self._ttft_ms is not None:
            event["ttft_ms"] = self._ttft_ms
        if exc_type is not None:
            self._metadata["error"] = True
            event.pop("response", None)  # a raised call has no trustworthy response payload
        if self._metadata:
            event["metadata"] = dict(self._metadata)
        self._track("model_call", event)
        return False  # never swallow the caller's exception
