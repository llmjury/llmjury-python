"""LLMJury Python SDK — deterministic ``assign`` + non-blocking ``track``.

See the module README and ``spec/bucketing.md`` for the frozen assignment contract.
"""

from __future__ import annotations

from .bucketing import AllocationSlice, assign_variant, bucket_of, murmur3_x86_32
from .buffer import EventBuffer
from .client import Client, HttpTransport, IngestAck, PromptAssignment, Transport, VariantVariables
from .intercept import ModelCall
from .offline import OfflineBuffer
from .wrap import WrappedClient

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Transport",
    "HttpTransport",
    "IngestAck",
    "PromptAssignment",
    "ModelCall",
    "VariantVariables",
    "WrappedClient",
    "EventBuffer",
    "OfflineBuffer",
    "AllocationSlice",
    "assign_variant",
    "bucket_of",
    "murmur3_x86_32",
    "__version__",
]
