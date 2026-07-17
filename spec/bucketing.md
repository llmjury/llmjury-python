# Frozen bucketing spec

This is the **frozen, language-agnostic** algorithm that assigns a user to a variant. All LLMJury
SDKs (Python, TypeScript, Java) and the LLMJury backend implement it identically, so an assignment
made in any language — or offline — is reproducible everywhere. The cross-language conformance
fixture is [`fixtures/bucketing-cases.json`](fixtures/bucketing-cases.json). Changing any rule in
this document is a breaking contract change and will not happen within a major version.

## 1. Hash

`MurmurHash3_x86_32(key, seed = 0)` interpreted as an **unsigned 32-bit** integer.

- **seed** is fixed at `0` in v1.
- **key** is the UTF-8 bytes of the string `"{salt}:{user_id}:{experiment_id}"` — the three values
  joined by a single ASCII colon (`:`), in that order. No trimming, no case-folding, no normalization.
- The algorithm is the canonical MurmurHash3 x86 32-bit (Austin Appleby). The reference matches the
  `mmh3` C library bit-for-bit (verified over 20k random + unicode inputs).

## 2. Bucket

```
bucket = MurmurHash3_x86_32(key, 0) % bucket_count
```

- `bucket` is in `[0, bucket_count)`.
- **`bucket_count`** is a **per-experiment config field** (default **1,000** = 0.1% granularity),
  carried in the experiment config that SDKs fetch. It is **never hardcoded in an SDK** and is
  identical across all languages for a given experiment. Finer granularity (e.g. 10,000) is
  allowed per experiment.

## 3. Variant selection (integer cumulative boundaries)

`allocation` is an **ordered** list of `{ variant, weight }` (integer weights). Order is significant.

```
total = sum(weight)
for each slice i in order:
    cumulative_i = sum(weight_0 .. weight_i)
    boundary_i   = floor(cumulative_i * bucket_count / total)     # integer arithmetic only
    if bucket < boundary_i: return slice_i.variant
return last slice.variant                                          # absorbs any remainder
```

Integer-only arithmetic (multiply before divide, floor) guarantees all SDKs agree with no
floating-point rounding divergence. Because the final cumulative equals `total`, the last boundary
equals `bucket_count`, so every bucket maps to exactly one variant.

> Example: `bucket_count = 1000`, allocation `[{control,90},{treatment,10}]` → control owns buckets
> `0..899`, treatment owns `900..999`.

## 4. Pinning & versioning

`salt` and `bucket_count` are **pinned for the life of an experiment version**. Changing either on a
running experiment re-buckets users, so it is only allowed by creating a **new experiment version**
(treated like a salt rotation: audited + acknowledged). Salt policy is **per-experiment**.

Assignments are **sticky**: adding an arm draws traffic from previously-unallocated buckets via a
bucket-reservation scheme and never remaps existing cumulative boundaries, so already-exposed users
keep their variant.

## 5. Conformance

Every SDK ships a test that reads `fixtures/bucketing-cases.json` and asserts that, for each case's
`(salt, user_id, experiment_id, bucket_count, allocation)`, its implementation returns
`expected_variant`. This is the mandatory cross-language determinism test; it gates every release.
