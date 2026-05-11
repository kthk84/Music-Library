"""Schema models for SoundBridge v2 — documents and validates persisted state.

The single source of truth is `shazam_status_cache.json` (per AGENTS.md). This
module gives that file a typed shape and a non-fatal `validate_status()` so
write-time anomalies surface in logs instead of corrupting the cache silently.
"""
from .status_schema import StatusCache, validate_status, STATUS_REQUIRED_KEYS, STATUS_OPTIONAL_KEYS

__all__ = [
    "StatusCache",
    "validate_status",
    "STATUS_REQUIRED_KEYS",
    "STATUS_OPTIONAL_KEYS",
]
