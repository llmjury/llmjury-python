"""``get_prompt``: variant prompt when configured, the in-code default on any failure path."""

from __future__ import annotations

from conftest import FailingTransport, RecordingTransport

from llmjury import Client

DEFAULT = "You are a helpful assistant."

CONFIG = [
    {
        "id": "exp_p",
        "salt": "s",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {"variant": "control", "weight": 50, "prompt": "Control prompt."},
            {"variant": "treatment", "weight": 50, "prompt": "Treatment prompt."},
        ],
    }
]


def test_get_prompt_returns_the_variant_prompt() -> None:
    client = Client("pk_test", transport=RecordingTransport(CONFIG))
    assert client.refresh_config("exp_p") is True
    result = client.get_prompt("exp_p", "user-0", default=DEFAULT)
    assert result.variant in {"control", "treatment"}
    assert result.fallback is False
    expected = "Control prompt." if result.variant == "control" else "Treatment prompt."
    assert result.prompt == expected
    client.close()


def test_get_prompt_falls_back_to_the_default_when_unreachable() -> None:
    # LLMJury down / config never fetched: the caller's in-code default keeps the app working.
    client = Client("pk_test", transport=FailingTransport())
    result = client.get_prompt("exp_p", "user-0", default=DEFAULT)
    assert result.variant is None
    assert result.fallback is True
    assert result.prompt == DEFAULT
    client.close()


def test_get_prompt_falls_back_when_the_variant_has_no_prompt() -> None:
    promptless = [
        {
            "id": "exp_np",
            "salt": "s",
            "bucket_count": 1000,
            "version": 1,
            "allocation": [
                {"variant": "control", "weight": 50},
                {"variant": "treatment", "weight": 50},
            ],
        }
    ]
    client = Client("pk_test", transport=RecordingTransport(promptless))
    assert client.refresh_config("exp_np") is True
    result = client.get_prompt("exp_np", "user-0", default=DEFAULT)
    assert result.variant in {"control", "treatment"}  # assignment still works
    assert result.fallback is True
    assert result.prompt == DEFAULT
    client.close()
