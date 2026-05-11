"""Track-key normalization helpers.

Used everywhere in v2 — by `_set_url_variants`, `_set_cover_hash_variants`,
the matching pipeline, the playbar lookup, etc. They're pure: take a string,
return a string. No I/O, no Flask, no globals.

History
-------
These three were defined inline in `app.py` (lines 1276 + 3487 + 3497 of the
pre-extraction version). Moved here so `lib/covers.py` could import them
without dragging app.py back into the import graph.

Companion definitions exist in `shazam_cache.py` (`_strip_all_parens`,
`_deep_norm_key`) — those should be deduplicated to import from here in a
follow-up. Today they are kept as-is to avoid changing shazam_cache.py.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict


def _strip_all_parens(key: str) -> str:
    """Remove all (...) and [...] segments, collapse whitespace.

    For cross-matching e.g. ``mOat (UK) - Guard Your Joy (Extended Mix)`` →
    ``mOat - Guard Your Joy``.
    """
    s = (key or "").strip()
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or key or ""


def _deep_norm_key(key: str) -> str:
    """Deep normalize: strip parens, fold accents, lowercase, unify '&' / ',',
    sort artists.

    Lets `Âme - Track` match `ame - track` and `Artist1 & Artist2 - Title`
    match `Artist2, Artist1 - Title`.
    """
    s = _strip_all_parens(key)
    # Fold accents so 'Âme' matches 'Ame' across sources (Soundeo vs Shazam).
    s = ''.join(ch for ch in unicodedata.normalize('NFKD', s) if not unicodedata.combining(ch))
    s = s.lower().replace(' & ', ', ')
    if ' - ' in s:
        artist_part, title_part = s.split(' - ', 1)
        artists = sorted(a.strip() for a in artist_part.split(', ') if a.strip())
        s = ', '.join(artists) + ' - ' + title_part
    return s


def _track_key_norm(t: Dict) -> tuple:
    """Normalized (artist, title) tuple for deduplication and set lookups."""
    return (t.get('artist', '').strip().lower(), t.get('title', '').strip().lower())
