"""Track-key normalization helpers."""
from __future__ import annotations

import re
import unicodedata
from typing import Dict


def _strip_all_parens(key: str) -> str:
    """Remove all (...) and [...] segments, collapse whitespace."""
    s = (key or "").strip()
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or key or ""


def _deep_norm_key(key: str) -> str:
    """Strip parens, fold accents, lowercase, unify '&'/',', sort artists."""
    s = _strip_all_parens(key)
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
