"""Interception: implicit metrics (latency, TTFT, tokens, errors) captured with no track calls."""

from __future__ import annotations

import pytest
from conftest import RecordingTransport

from llmjury import Client
from llmjury.intercept import extract_response_fields

CONFIG = [
    {
        "id": "exp_i",
        "name": "intercept-demo",
        "salt": "s",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {"variant": "control", "weight": 50},
            {"variant": "treatment", "weight": 50},
        ],
    }
]


class _OpenAIUsage:
    prompt_tokens = 64
    completion_tokens = 96


class _OpenAIMessage:
    content = "openai reply"


class _OpenAIChoice:
    message = _OpenAIMessage()


class _OpenAIResponse:
    model = "gpt-x"
    usage = _OpenAIUsage()
    choices = [_OpenAIChoice()]


class _AnthropicUsage:
    input_tokens = 40
    output_tokens = 70


class _AnthropicBlock:
    text = "anthropic reply"


class _AnthropicResponse:
    model = "claude-x"
    usage = _AnthropicUsage()
    content = [_AnthropicBlock()]


def test_extraction_understands_openai_and_anthropic_shapes() -> None:
    openai = extract_response_fields(_OpenAIResponse())
    assert openai == {
        "model": "gpt-x",
        "tokens_input": 64,
        "tokens_output": 96,
        "response": "openai reply",
    }
    anthropic = extract_response_fields(_AnthropicResponse())
    assert anthropic == {
        "model": "claude-x",
        "tokens_input": 40,
        "tokens_output": 70,
        "response": "anthropic reply",
    }
    assert extract_response_fields("plain text") == {"response": "plain text"}
    assert extract_response_fields(object()) == {}


def _events(transport: RecordingTransport, client: Client) -> list[dict]:
    client.flush(timeout=2.0)
    client.close()
    return transport.sent_events


def test_intercept_tracks_exposure_and_model_call_with_latency() -> None:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("intercept-demo") is True

    with client.intercept_model_call("intercept-demo", "u-1") as call:
        call.mark_first_token()
        call.record(_AnthropicResponse(), prompt="the prompt")

    events = _events(transport, client)
    assert [e["type"] for e in events] == ["exposure", "model_call"]
    exposure, model_call = events
    assert exposure["experiment_id"] == "exp_i"  # name normalized to the canonical id
    assert exposure["variant"] in {"control", "treatment"}
    assert model_call["latency_ms"] >= 0
    assert model_call["ttft_ms"] >= 0
    assert model_call["tokens_input"] == 40
    assert model_call["tokens_output"] == 70
    assert model_call["model"] == "claude-x"
    assert model_call["prompt"] == "the prompt"
    assert model_call["response"] == "anthropic reply"


def test_intercept_records_the_error_and_reraises() -> None:
    transport = RecordingTransport(CONFIG)
    client = Client("pk_test", transport=transport)
    assert client.refresh_config("intercept-demo") is True

    with pytest.raises(RuntimeError):
        with client.intercept_model_call("intercept-demo", "u-err") as call:
            call.record(prompt="the prompt")
            raise RuntimeError("provider blew up")

    events = _events(transport, client)
    model_call = events[-1]
    assert model_call["type"] == "model_call"
    assert model_call["metadata"]["error"] is True
    assert "response" not in model_call  # a raised call has no trustworthy response
    assert model_call["latency_ms"] >= 0  # failures still measure latency


def test_intercept_records_nothing_on_the_fallback_path() -> None:
    # Config unreachable → assign resolves None → the block runs untouched, nothing is tracked.
    transport = RecordingTransport()  # serves no config
    client = Client("pk_test", transport=transport)
    with client.intercept_model_call("intercept-demo", "u-1") as call:
        call.record("still works")
    client.flush(timeout=2.0)
    client.close()
    assert transport.sent_events == []
