# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-16

Initial public release.

### Added

- `Client` with deterministic, local-compute `assign(experiment, user)` — no network on the hot
  path — and non-blocking, buffered `track(event, payload)`.
- `get_prompt(experiment, user, default)` — variant prompt with an in-code fallback that survives
  a full LLMJury outage.
- `get_variables(experiment, user, defaults)` — per-variant configuration merged over defaults.
- `wrap(provider_client, experiment)` — setup-once interception of OpenAI/Anthropic-shaped model
  calls (latency, tokens, errors, time-to-first-token), plus `as_user(...)` ambient user scoping
  and the explicit `intercept_model_call(...)` context manager.
- Async variants `aassign` / `atrack`.
- Versioned config polling with `If-None-Match`/`304` and immediate refresh when ingest reports a
  newer `config_version`.
- Bounded in-memory event buffer with background flush, bounded retry, and file-backed offline
  spill/replay (24h bound, original timestamps).
- Frozen cross-language bucketing contract (`spec/bucketing.md`) with the shared conformance
  fixture — identical assignments across Python, TypeScript, and Java.
- Zero required runtime dependencies; Python 3.8+.

[0.1.0]: https://github.com/llmjury/llmjury-python/releases/tag/v0.1.0
