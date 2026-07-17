"""Setup-once wrap(): provider calls are traced with zero call-site code; variables from memory."""

from __future__ import annotations

import pytest
from conftest import RecordingTransport

from llmjury import Client

CONFIG = [
    {
        "id": "exp_w",
        "name": "wrap-demo",
        "salt": "s",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {
                "variant": "control",
                "weight": 50,
                "prompt": "Control prompt.",
                "variables": {"model": "claude-haiku-4-5", "temperature": "0.2"},
            },
            {
                "variant": "treatment",
                "weight": 50,
                "prompt": "Treatment prompt.",
                "variables": {"model": "claude-sonnet-5", "temperature": "0.7"},
            },
        ],
    }
]


class _Usage:
    input_tokens = 40
    output_tokens = 70


class _Block:
    text = "a reply"


class _FakeMessages:
    """Anthropic-shaped `client.messages` namespace."""

    def create(self, **kwargs):
        attrs = {"model": kwargs.get("model"), "usage": _Usage(), "content": [_Block()]}
        return type("R", (), attrs)()


class _FakeProvider:
    messages = _FakeMessages()

    def close(self):
        return "closed"


def _ready_client() -> tuple[Client, RecordingTransport]:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("wrap-demo") is True
    return client, transport


def test_wrapped_calls_are_traced_with_no_call_site_code() -> None:
    client, transport = _ready_client()
    llm = client.wrap(_FakeProvider(), experiment="wrap-demo")

    with client.as_user("u-1"):
        v = client.get_variables("wrap-demo", "u-1", defaults={"model": "fallback-model"})
        response = llm.messages.create(
            model=v.values["model"], messages=[{"role": "user", "content": "hi"}]
        )

    assert response.content[0].text == "a reply"
    client.flush(timeout=2.0)
    client.close()
    assert [e["type"] for e in transport.sent_events] == ["exposure", "model_call"]
    model_call = transport.sent_events[1]
    assert model_call["experiment_id"] == "exp_w"
    assert model_call["variant"] == v.variant
    assert model_call["latency_ms"] >= 0
    assert model_call["tokens_output"] == 70
    assert model_call["prompt"] == "hi"  # judge input pulled from the messages
    assert model_call["model"] == v.values["model"]  # the variant's configured model was used


def test_non_model_calls_and_userless_calls_pass_through_untracked() -> None:
    client, transport = _ready_client()
    llm = client.wrap(_FakeProvider(), experiment="wrap-demo")

    assert llm.messages.create(model="m", messages=[]) is not None  # no ambient user
    with client.as_user("u-1"):
        assert llm.close() == "closed"  # not a model call
    client.flush(timeout=2.0)
    client.close()
    assert transport.sent_events == []


def test_wrapped_errors_are_recorded_and_reraised() -> None:
    client, transport = _ready_client()

    class _Exploding:
        def create(self, **kwargs):
            raise TimeoutError("provider timeout")

    llm = client.wrap(_Exploding(), experiment="wrap-demo")
    with client.as_user("u-err"):
        with pytest.raises(TimeoutError):
            llm.create(model="m", messages=[{"role": "user", "content": "hi"}])
    client.flush(timeout=2.0)
    client.close()
    model_call = transport.sent_events[-1]
    assert model_call["metadata"]["error"] is True
    assert model_call["latency_ms"] >= 0


def test_get_variables_merges_defaults_from_client_memory() -> None:
    client, transport = _ready_client()
    v = client.get_variables("wrap-demo", "u-1", defaults={"model": "fallback", "top_p": "1.0"})
    assert v.variant in {"control", "treatment"}
    assert v.fallback is False
    assert v.values["model"] in {"claude-haiku-4-5", "claude-sonnet-5"}  # configured wins
    assert v.values["top_p"] == "1.0"  # default preserved
    assert v.values["prompt"] in {"Control prompt.", "Treatment prompt."}
    assert transport.config_calls == 1  # resolved from memory — no extra API call
    client.close()


def test_get_variables_falls_back_to_defaults_when_unreachable() -> None:
    client = Client("pk_test", transport=RecordingTransport())  # serves no config
    v = client.get_variables("wrap-demo", "u-1", defaults={"model": "fallback"})
    assert v.variant is None
    assert v.fallback is True
    assert v.values == {"model": "fallback"}
    client.close()
