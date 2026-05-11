"""Pure-function helpers extracted from app.py — no Flask, no threading,
no app-attribute access. Safe to import from anywhere.

Why this package exists
-----------------------
`app.py` was a 6,200-line god-file. The audit (2026-04-29) called for splitting
it into `routes/` (Flask blueprints) + `jobs/` (background workers) + `lib/`
(pure helpers). The cover-art surface was the first slice extracted because (a)
it was self-contained, (b) it had been actively touched in sessions 3, 5, 7
so the code was fresh in mind, (c) extracting it unblocked a future
`routes/sync_covers.py` blueprint that needs these helpers.
"""
from .keys import _strip_all_parens, _deep_norm_key, _track_key_norm
from .covers import (
    _get_cover_cache_dir,
    _cache_cover_art,
    _set_cover_hash_variants,
    _cover_hashes_for_status,
    _rebuild_cover_hashes_from_disk,
    _extract_local_artwork_to_cache,
    download_cover_art,
)

__all__ = [
    # keys
    "_strip_all_parens",
    "_deep_norm_key",
    "_track_key_norm",
    # covers
    "_get_cover_cache_dir",
    "_cache_cover_art",
    "_set_cover_hash_variants",
    "_cover_hashes_for_status",
    "_rebuild_cover_hashes_from_disk",
    "_extract_local_artwork_to_cache",
    "download_cover_art",
]
