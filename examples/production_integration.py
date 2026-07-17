"""Production LLM integration: the full recommended pattern.

Setup-once ``wrap()`` interception + ``get_prompt`` + one explicit business-outcome event.
The wrapped provider client records exposure, latency, tokens, model, and errors for every
call — with zero per-call tracking code.

This example fakes both the LLMJury backend and the LLM provider so it runs offline; the
integration code between the markers is exactly what you'd ship (with the real
``anthropic.Anthropic()`` / ``openai.OpenAI()`` client and no ``transport=``).
"""

from _fake_backend import FakeBackend

from llmjury import Client

CONFIG = [
    {
        "id": "exp_checkout",
        "name": "checkout-prompt",
        "salt": "salt-checkout-v1",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {"variant": "control", "weight": 50, "prompt": "You are a helpful assistant."},
            {
                "variant": "friendly",
                "weight": 50,
                "prompt": "You are a warm, upbeat shopping guide. Keep answers short.",
            },
        ],
    }
]


class FakeUsage:
    input_tokens = 512
    output_tokens = 128


class FakeResponse:
    usage = FakeUsage()
    model = "claude-sonnet-5"
    content = "Sure — here's how to finish checking out."


class FakeMessages:
    def create(self, **kwargs):  # looks like anthropic's client.messages.create
        return FakeResponse()


class FakeAnthropicClient:
    messages = FakeMessages()


def main() -> None:
    # ---- setup, once at startup (real app: Client(experiments=["checkout-prompt"])) ----
    client = Client(transport=FakeBackend(CONFIG), experiments=["exp_checkout"])
    client.refresh_config("exp_checkout")
    llm = client.wrap(FakeAnthropicClient(), "checkout-prompt")

    # ---- per request --------------------------------------------------------------
    user_id = "user-42"
    with client.as_user(user_id):
        p = client.get_prompt("checkout-prompt", user_id, default="You are a helpful assistant.")
        print(f"variant={p.variant} prompt={p.prompt!r}")

        # Call the provider client DIRECTLY — the wrapper records exposure + model_call
        # (latency, tokens, model, errors) automatically.
        response = llm.messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            system=p.prompt,
            messages=[{"role": "user", "content": "How do I check out?"}],
        )
        print(f"llm answered: {response.content!r}")

    # ---- when the outcome happens (often a different request) ---------------------
    client.track(
        "business_event",
        {
            "experiment_id": "checkout-prompt",
            "user_id": user_id,
            "variant": p.variant,
            "business_metric": "conversion",
            "value": 1,
        },
    )

    client.flush()
    client.close()


if __name__ == "__main__":
    main()
