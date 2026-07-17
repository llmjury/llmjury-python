"""Setup-once interception: wrap a provider client and call it directly (OkHttp-interceptor style).

Configure once at startup::

    llm = client.wrap(openai_client, experiment="checkout-copy")

then per request set the ambient user (e.g. in web middleware) and call the provider client
exactly as you always do — no per-call wrapping, no arranging::

    with client.as_user(user_id):
        response = llm.chat.completions.create(model=v["model"], messages=[...])

Every call made through the wrapper is timed; calls that look like model invocations (their kwargs
carry ``messages``/``prompt``/``model``, or their result exposes usage/content) are recorded as an
exposure + ``model_call`` with latency, tokens, model, and ``metadata.error`` on exceptions —
exactly like :meth:`~llmjury.Client.intercept_model_call`, but with zero call-site code. Non-model
calls (``.close()``, config accessors, …) pass through untracked. Without an ambient user the call
passes through untouched (nothing is recorded — there is no one to attribute it to).

The wrapper is a duck-typed attribute proxy: it works with OpenAI- and Anthropic-style clients (or
anything shaped like them) without importing any provider SDK.
"""

from __future__ import annotations

from typing import Any, Callable

from .intercept import extract_response_fields

__all__ = ["WrappedClient"]

#: kwargs that mark a call as a model invocation even before we see the result.
_MODEL_CALL_KWARGS = ("messages", "prompt", "model", "input")


def _prompt_from_kwargs(kwargs: dict) -> str | None:
    """Best-effort prompt text for the judge: the last message content, or the prompt/input arg."""
    messages = kwargs.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
        if isinstance(content, str):
            return content
    for key in ("prompt", "input"):
        value = kwargs.get(key)
        if isinstance(value, str):
            return value
    return None


def _looks_like_model_call(kwargs: dict, fields: dict) -> bool:
    # Model-shaped kwargs OR token usage on the result. A bare string result is NOT enough on its
    # own — plenty of non-model methods return strings (e.g. `.close()`).
    return any(key in kwargs for key in _MODEL_CALL_KWARGS) or bool(
        fields.keys() & {"tokens_input", "tokens_output"}
    )


class _WrappedCallable:
    """A provider method, timed and recorded when the call turns out to be a model invocation."""

    def __init__(self, fn: Callable, client: Any, experiment: str) -> None:
        self._fn = fn
        self._client = client
        self._experiment = experiment

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        user = self._client.current_user()
        if user is None:
            return self._fn(*args, **kwargs)  # nobody to attribute to — pass through untouched
        call = self._client.intercept_model_call(self._experiment, user)
        call.__enter__()
        try:
            result = self._fn(*args, **kwargs)
        except Exception:
            if any(key in kwargs for key in _MODEL_CALL_KWARGS):
                call.record(prompt=_prompt_from_kwargs(kwargs), model=kwargs.get("model"))
                call.__exit__(Exception, None, None)  # records metadata.error, keeps latency
            raise
        fields = extract_response_fields(result)
        if _looks_like_model_call(kwargs, fields):
            call.record(
                result,
                prompt=_prompt_from_kwargs(kwargs),
                model=fields.get("model") or kwargs.get("model"),
            )
            call.__exit__(None, None, None)
        # else: not a model call (e.g. .close()) — record nothing.
        return result


class WrappedClient:
    """Transparent attribute proxy over a provider client (returned by ``Client.wrap``)."""

    def __init__(self, target: Any, client: Any, experiment: str) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_experiment", experiment)

    def __getattr__(self, name: str) -> Any:
        value = getattr(object.__getattribute__(self, "_target"), name)
        client = object.__getattribute__(self, "_client")
        experiment = object.__getattribute__(self, "_experiment")
        if callable(value):
            return _WrappedCallable(value, client, experiment)
        # Namespace objects (client.chat, client.messages, …) stay wrapped so leaf calls are traced.
        if hasattr(value, "__dict__") or hasattr(value, "__getattr__"):
            return WrappedClient(value, client, experiment)
        return value

    def __repr__(self) -> str:
        return f"WrappedClient({object.__getattribute__(self, '_target')!r})"
