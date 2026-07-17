# Contributing to the LLMJury Python SDK

Thanks for your interest in improving the SDK! This guide covers local setup, the quality bar,
and the one rule that is different from most projects: the frozen bucketing contract.

## Development setup

```bash
git clone https://github.com/llmjury/llmjury-python.git
cd llmjury-python
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Running checks

```bash
ruff check .          # lint
black --check .       # formatting (line length 100)
pytest                # full test suite
```

All three must pass; CI runs them on Python 3.8 through 3.13.

## The frozen bucketing contract

`src/llmjury/bucketing.py` implements the frozen, cross-language assignment algorithm described in
[`spec/bucketing.md`](spec/bucketing.md). The Python, TypeScript, and Java SDKs must return the
**same variant for the same input, bit for bit** — that determinism is a core product guarantee.

- Do **not** change anything in `bucketing.py` or `spec/` in a regular PR. Any behavioral change
  there is a breaking contract change and is coordinated across all SDKs and the backend by the
  maintainers.
- `tests/test_determinism.py` asserts every case in `spec/fixtures/bucketing-cases.json`. If your
  change breaks it, the change is wrong — not the fixture.

## SDK design rules

These invariants hold everywhere in the SDK; PRs that violate them will be asked to change:

1. **Never block the host app.** `assign` is pure local compute; `track` only appends to a buffer.
2. **Never throw into the host app.** Network and I/O failures are logged and swallowed; the
   in-code `default` on `get_prompt` must always work.
3. **Zero required runtime dependencies.** The default transport uses only the standard library.
4. **No real network in tests.** Use the in-process transports in `tests/conftest.py`.

## Submitting changes

1. Fork and create a topic branch.
2. Add or update tests for anything you change.
3. Keep the public API backwards-compatible; deprecate before removing.
4. Update `CHANGELOG.md` under an `Unreleased` heading.
5. Open a PR with a clear description of the motivation and behavior change.

For anything non-trivial, open an issue first so we can agree on the approach before you invest
time in the code.

## Reporting issues

- Bugs and feature requests: [GitHub issues](https://github.com/llmjury/llmjury-python/issues)
- Security vulnerabilities: see [SECURITY.md](SECURITY.md) — please do not open public issues.
