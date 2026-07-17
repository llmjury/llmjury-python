"""Package smoke test: the public surface imports and exposes a version (proves CI wiring)."""

from __future__ import annotations

import llmjury


def test_public_surface() -> None:
    assert isinstance(llmjury.__version__, str)
    for name in ("Client", "assign_variant", "bucket_of", "murmur3_x86_32", "OfflineBuffer"):
        assert hasattr(llmjury, name)
