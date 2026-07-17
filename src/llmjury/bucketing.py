"""Frozen bucketing hash — the language-agnostic assignment algorithm (spec/bucketing.md).

This is a direct port of the LLMJury reference implementation. It MUST stay bit-for-bit
identical to the TypeScript and Java SDKs and the backend assignment service: the same
``(salt, user_id, experiment_id, bucket_count, allocation)`` always resolves to the same variant.
The cross-language determinism fixture (``spec/fixtures/bucketing-cases.json``) is the merge
gate. Changing any rule here is a breaking contract change.

Intentionally dependency-free and integer-only so there is no floating-point divergence.
"""

from __future__ import annotations

from typing import TypedDict

_MASK32 = 0xFFFFFFFF
_C1 = 0xCC9E2D51
_C2 = 0x1B873593


class AllocationSlice(TypedDict):
    """One ordered allocation slice: a variant key and its integer weight."""

    variant: str
    weight: int


def _rotl32(x: int, r: int) -> int:
    return ((x << r) | (x >> (32 - r))) & _MASK32


def murmur3_x86_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x86 32-bit (Austin Appleby). Returns an unsigned 32-bit int; seed fixed at 0."""
    length = len(data)
    h1 = seed & _MASK32
    rounded_end = length & ~0x03  # largest multiple of 4 <= length

    for i in range(0, rounded_end, 4):
        k1 = (
            (data[i] & 0xFF)
            | ((data[i + 1] & 0xFF) << 8)
            | ((data[i + 2] & 0xFF) << 16)
            | ((data[i + 3] & 0xFF) << 24)
        )
        k1 = (k1 * _C1) & _MASK32
        k1 = _rotl32(k1, 15)
        k1 = (k1 * _C2) & _MASK32
        h1 ^= k1
        h1 = _rotl32(h1, 13)
        h1 = (h1 * 5 + 0xE6546B64) & _MASK32

    k1 = 0
    tail = length & 0x03
    if tail == 3:
        k1 ^= (data[rounded_end + 2] & 0xFF) << 16
    if tail >= 2:
        k1 ^= (data[rounded_end + 1] & 0xFF) << 8
    if tail >= 1:
        k1 ^= data[rounded_end] & 0xFF
        k1 = (k1 * _C1) & _MASK32
        k1 = _rotl32(k1, 15)
        k1 = (k1 * _C2) & _MASK32
        h1 ^= k1

    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & _MASK32
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & _MASK32
    h1 ^= h1 >> 16
    return h1 & _MASK32


def bucket_of(salt: str, user_id: str, experiment_id: str, bucket_count: int) -> int:
    """Map a user to a bucket in ``[0, bucket_count)``.

    ``bucket_count`` is a per-experiment config value (default 1000) carried in the fetched config —
    it is never hardcoded in the SDK.
    """
    key = f"{salt}:{user_id}:{experiment_id}".encode()
    return murmur3_x86_32(key) % bucket_count


def assign_variant(
    salt: str,
    user_id: str,
    experiment_id: str,
    bucket_count: int,
    allocation: list[AllocationSlice],
) -> str:
    """Deterministically assign a variant key using integer-only cumulative boundaries.

    The boundary for slice ``i`` is ``floor(cumulative_weight_i * bucket_count / total_weight)``; a
    bucket belongs to the first slice whose boundary it falls below. The final slice absorbs any
    remainder, so every bucket maps to exactly one variant.
    """
    bucket = bucket_of(salt, user_id, experiment_id, bucket_count)
    total = sum(slice_["weight"] for slice_ in allocation)
    cumulative = 0
    for slice_ in allocation:
        cumulative += slice_["weight"]
        boundary = (cumulative * bucket_count) // total
        if bucket < boundary:
            return slice_["variant"]
    return allocation[-1]["variant"]
