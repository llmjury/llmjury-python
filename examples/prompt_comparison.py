"""Prompt comparison: A/B test two system prompts with get_prompt.

Each variant carries its prompt in the experiment config (managed in the dashboard). Your in-code
``default`` is the guaranteed fallback — it is what users get during a cold start, an outage, or
if the variant has no prompt configured. Runs completely offline (see _fake_backend.py).
"""

from _fake_backend import FakeBackend

from llmjury import Client

CONFIG = [
    {
        "id": "exp_support_tone",
        "name": "support-tone",
        "salt": "salt-tone-v1",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {
                "variant": "concise",
                "weight": 50,
                "prompt": "You are a support agent. Answer in at most two sentences.",
            },
            {
                "variant": "empathetic",
                "weight": 50,
                "prompt": "You are a warm, patient support agent. Acknowledge feelings first.",
            },
        ],
    }
]

DEFAULT_PROMPT = "You are a helpful support agent."


def main() -> None:
    client = Client(transport=FakeBackend(CONFIG), experiments=["exp_support_tone"])
    client.refresh_config("exp_support_tone")

    for user in ["alice", "bob", "carol"]:
        p = client.get_prompt("support-tone", user, default=DEFAULT_PROMPT)
        # p.variant -> which arm; p.prompt -> the text to use as your system prompt;
        # p.fallback -> True when the default was used (outage / cold start).
        print(f"{user}: variant={p.variant} fallback={p.fallback}\n  system prompt: {p.prompt}")

        # ... call your LLM with p.prompt as the system prompt, then track the outcome
        # that decides the experiment (e.g. the customer rated the answer helpful):
        client.track(
            "business_event",
            {
                "experiment_id": "support-tone",
                "user_id": user,
                "variant": p.variant,
                "business_metric": "helpful_rating",
                "value": 1,
            },
        )

    client.flush()
    client.close()


if __name__ == "__main__":
    main()
