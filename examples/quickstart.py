"""Basic setup + your first experiment: assign users to variants and track events.

Runs completely offline (see _fake_backend.py). In a real app:

    export LLMJURY_API_KEY=llmj_pk_...     # dashboard -> Settings -> API keys
    client = Client(experiments=["checkout-prompt"])
"""

from _fake_backend import FakeBackend

from llmjury import Client

CONFIG = [
    {
        "id": "exp_checkout",
        "name": "checkout-prompt",
        "salt": "salt-checkout",
        "bucket_count": 1000,
        "version": 1,
        "allocation": [
            {"variant": "control", "weight": 90},
            {"variant": "treatment", "weight": 10},
        ],
    }
]


def main() -> None:
    client = Client(transport=FakeBackend(CONFIG), experiments=["exp_checkout"])
    client.refresh_config("exp_checkout")  # warm the cache synchronously (startup only)

    for user in ["user-1", "user-2", "user-3", "user-4", "user-5"]:
        # Pure local compute — no network call. Deterministic: the same user
        # always lands in the same variant, in every SDK language.
        variant = client.assign("exp_checkout", user)
        print(f"assign({user}) -> {variant}")
        client.track(
            "exposure", {"experiment_id": "exp_checkout", "user_id": user, "variant": variant}
        )

    client.flush()
    client.close()
    print("done — in a real app the dashboard now shows these exposures")


if __name__ == "__main__":
    main()
