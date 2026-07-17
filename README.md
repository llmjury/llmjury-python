# LLMJury Python SDK

[![CI](https://github.com/llmjury/llmjury-python/actions/workflows/ci.yml/badge.svg)](https://github.com/llmjury/llmjury-python/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/llmjury-sdk)](https://pypi.org/project/llmjury-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/llmjury-sdk)](https://pypi.org/project/llmjury-sdk/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

The official Python SDK for [LLMJury](https://llmjury.com) — run LLM experiments in production.

LLMJury lets you compare prompts and models on real traffic, with real users, and measure what
actually matters: your business outcomes. The SDK assigns each user to an experiment variant
deterministically, captures latency / tokens / errors from your existing LLM calls automatically,
and streams everything to the LLMJury dashboard where the stats engine tells you which variant
wins — and when you have enough data to trust it.

**Why teams use it:**

- **Ship prompt and model changes like feature flags.** Roll a new prompt to 10% of traffic,
  watch the metrics, roll forward or back — no redeploy.
- **Decide on outcomes, not vibes.** Tie each variant to conversion, retention, resolution rate —
  whatever your business metric is — with statistical rigor built in.
- **Zero overhead on the hot path.** Variant assignment is pure local compute (no network call),
  and tracking never blocks and never throws into your app. A full LLMJury outage degrades to
  your in-code defaults.

## Installation

```bash
pip install llmjury-sdk
```

Python 3.8+. **Zero runtime dependencies** — the SDK uses only the standard library.

## Authentication

Get your **publishable API key** from the LLMJury dashboard (**Settings → API keys**, it looks
like `llmj_pk_...`) and export it:

```bash
export LLMJURY_API_KEY=llmj_pk_...
```

The publishable key is write-only and rate-limited, so it is safe to ship in any environment.
The SDK reads it automatically; you can also pass `Client(api_key="llmj_pk_...")` explicitly.

## Quick start

Create an experiment in the dashboard (say, `checkout-prompt`, with variants `control` and
`friendly`, each carrying a prompt). Then:

```python
from llmjury import Client

# Once, at startup. Prefetching means the first assign resolves instantly.
client = Client(experiments=["checkout-prompt"])

# Per request: which variant is this user in, and what prompt does it carry?
p = client.get_prompt("checkout-prompt", user_id, default="You are a helpful assistant.")
print(p.variant, p.prompt)  # e.g. "friendly", "You are a warm, upbeat shopping guide..."

# When the user converts, record the outcome — this is what the experiment is measured on.
client.track("business_event", {
    "experiment_id": "checkout-prompt",
    "user_id": user_id,
    "variant": p.variant,
    "business_metric": "conversion",
    "value": 1,
})
```

That's the whole loop: **assign → use the variant's prompt → track the outcome**. The dashboard
does the rest.

## Recommended production setup

Add the setup-once `wrap()` interceptor and the SDK also captures **latency, token usage, model
name, errors, and time-to-first-token** from every LLM call — with zero per-call code:

```python
from llmjury import Client
import anthropic

# ---- once, at startup -------------------------------------------------------
client = Client(experiments=["checkout-prompt"])
llm = client.wrap(anthropic.Anthropic(), "checkout-prompt")  # every model call is now traced

# ---- per request ------------------------------------------------------------
with client.as_user(user_id):
    p = client.get_prompt("checkout-prompt", user_id, default="You are a helpful assistant.")
    # Call your provider client DIRECTLY — metrics are intercepted automatically.
    response = llm.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=p.prompt,
        messages=[{"role": "user", "content": user_input}],
    )

# The ONLY explicit tracking you write is the business outcome:
client.track("business_event", {
    "experiment_id": "checkout-prompt", "user_id": user_id,
    "variant": p.variant, "business_metric": "conversion", "value": 1,
})
```

The wrapper is a duck-typed proxy — it works with OpenAI- and Anthropic-style clients (or anything
shaped like them) without importing any provider SDK.

## Core concepts

| Concept | What it is |
|---|---|
| **Experiment** | A named test with an ordered list of variants and traffic weights, defined in the dashboard. Address it by id or unique name. |
| **Variant** | One arm of the experiment. Carries a prompt and/or arbitrary variables (e.g. `model`, `temperature`). |
| **Assignment** | `assign(experiment, user)` → variant key. Deterministic: the same user always gets the same variant, in every SDK language, with no network call. |
| **User / session** | Any stable string id you choose — user id, session id, tenant id. It is hashed, never stored raw for assignment. |
| **Exposure** | "User X saw variant Y" — recorded automatically by `wrap`/interception, or track it yourself. |
| **Model call** | One LLM invocation: latency, tokens in/out, model, error — captured by the interceptor. |
| **Business event** | Your outcome metric (conversion, thumbs-up, resolution). The thing the experiment is judged on. |

### Comparing models

Variants can carry **variables**, so an experiment can vary the model (or temperature, or any
knob) instead of — or alongside — the prompt:

```python
v = client.get_variables("model-shootout", user_id,
                         defaults={"model": "claude-haiku-4-5", "temperature": "0.3"})
response = llm.messages.create(model=v.values["model"], ...)
```

### Comparing prompts

`get_prompt` is the purpose-built path: each variant carries a prompt, and your in-code `default`
is the guaranteed fallback. Rich prompt workflows (versioning, edit history, per-variant prompt
text) are managed in the dashboard.

### Manual control

The low-level primitives are always available if you want full control:

```python
variant = client.assign("checkout-prompt", user_id)   # just the variant key (or None pre-config)
client.track("exposure", {"experiment_id": "checkout-prompt", "user_id": user_id, "variant": variant})
client.track("model_call", {"experiment_id": "checkout-prompt", "user_id": user_id,
                            "variant": variant, "latency_ms": 840, "tokens_input": 512,
                            "tokens_output": 128, "model": "claude-sonnet-5"})
```

Async variants exist for both: `await client.aassign(...)`, `await client.atrack(...)`.

## Error handling & resilience

The SDK is engineered so that **LLMJury can never take your app down**:

- `assign` / `get_prompt` / `get_variables` never touch the network on the hot path — they compute
  against a locally cached config that refreshes in the background (60s polling, `ETag`/`304`).
- `track` appends to a bounded in-memory buffer and returns immediately. A background thread
  flushes every second (or every 1,000 events).
- Failed flushes retry a bounded number of times, then **spill to disk** (if `offline_path` is
  set) and replay later with their original timestamps (bounded to 24h), or drop. They never
  raise into your code.
- Before the first config fetch completes, `assign` returns `None` and `get_prompt` returns your
  `default` — your app keeps working during a cold start with the network down.

There are no exceptions to catch. The one deliberate consequence: telemetry is best-effort — if
your process is killed without `client.close()`, buffered events from the last flush interval may
be lost.

## Configuration

```python
Client(
    api_key=None,              # default: $LLMJURY_API_KEY
    base_url=None,             # default: $LLMJURY_BASE_URL, else https://api.llmjury.com
    experiments=None,          # experiment ids/names to prefetch at startup
    flush_interval=1.0,        # seconds between background flushes
    flush_size=1000,           # flush early when the buffer reaches this size
    config_poll_interval=60.0, # seconds between config refreshes
    offline_path=None,         # file path enabling offline spill/replay
    request_timeout=5.0,       # per-request timeout, seconds
)
```

Lifecycle: construct **one** `Client` per process and reuse it. Call `client.flush()` to drain
synchronously (e.g. at the end of a batch job) and `client.close()` at shutdown.

## Examples

Runnable scripts live in [`examples/`](examples/):

- [`quickstart.py`](examples/quickstart.py) — assign + track end-to-end (runs offline, no account needed)
- [`prompt_comparison.py`](examples/prompt_comparison.py) — A/B test two prompts with `get_prompt`
- [`model_comparison.py`](examples/model_comparison.py) — route traffic across models with `get_variables`
- [`production_integration.py`](examples/production_integration.py) — the full `wrap()` + business-outcome pattern

## The determinism guarantee

Assignment is a frozen, cross-language contract: MurmurHash3 over `salt:user:experiment` with
integer-only boundary arithmetic, specified in [`spec/bucketing.md`](spec/bucketing.md). The
Python, [TypeScript](https://github.com/llmjury/llmjury-typescript), and
[Java](https://github.com/llmjury/llmjury-java) SDKs all assert the same conformance fixture in
CI, so a user gets the same variant no matter which service — in which language — asks.

## Links

- [LLMJury docs](https://llmjury.com/docs) · [Dashboard](https://llmjury.com)
- [TypeScript SDK](https://github.com/llmjury/llmjury-typescript) · [Java SDK](https://github.com/llmjury/llmjury-java)
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## License

[Apache 2.0](LICENSE)
