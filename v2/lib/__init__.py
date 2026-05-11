"""Pure-function helpers extracted from app.py — no Flask, no threading,
no app-attribute access. Safe to import from anywhere.

Re-exports the public surface from `keys` and `covers` so callers can do
`from lib import _strip_all_parens` without knowing the submodule layout.
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
    "_strip_all_parens",
    "_deep_norm_key",
    "_track_key_norm",
    "_get_cover_cache_dir",
    "_cache_cover_art",
    "_set_cover_hash_variants",
    "_cover_hashes_for_status",
    "_rebuild_cover_hashes_from_disk",
    "_extract_local_artwork_to_cache",
    "download_cover_art",
]
