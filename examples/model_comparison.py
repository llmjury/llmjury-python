"""Model comparison: route traffic across two models with get_variables.

Variants carry arbitrary variables — here ``model`` and ``temperature`` — merged over your
in-code defaults, so the experiment can vary any knob without a redeploy. Runs completely
offline (see _fake_backend.py).
"""

from _fake_backend import FakeBackend

from llmjury import Client

CONFIG = [
    {
        "id": "exp_model_shootout",
        "name": "model-shootout",
        "salt": "salt-shootout-v1",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {
                "variant": "haiku",
                "weight": 50,
                "variables": {"model": "claude-haiku-4-5", "temperature": "0.3"},
            },
            {
                "variant": "sonnet",
                "weight": 50,
                "variables": {"model": "claude-sonnet-5", "temperature": "0.3"},
            },
        ],
    }
]


def main() -> None:
    client = Client(transport=FakeBackend(CONFIG), experiments=["exp_model_shootout"])
    client.refresh_config("exp_model_shootout")

    for user in ["alice", "bob", "carol"]:
        v = client.get_variables(
            "model-shootout", user, defaults={"model": "claude-haiku-4-5", "temperature": "0.3"}
        )
        model = v.values["model"]
        print(f"{user}: variant={v.variant} -> model={model} temperature={v.values['temperature']}")

        # ... call your LLM with v.values["model"] and track cost/latency/outcome. The dashboard
        # then answers: does the bigger model actually move the business metric enough
        # to justify its cost?
        client.track(
            "model_call",
            {
                "experiment_id": "model-shootout",
                "user_id": user,
                "variant": v.variant,
                "model": model,
                "latency_ms": 840,
                "tokens_input": 512,
                "tokens_output": 128,
            },
        )

    client.flush()
    client.close()


if __name__ == "__main__":
    main()
