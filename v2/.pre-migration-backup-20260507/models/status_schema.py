"""TypedDict + non-fatal validator for `shazam_status_cache.json`.

Why this exists
---------------
The status cache is the single source of truth for everything the Sync UI
shows: dot state (urls / not_found), starred-on-Soundeo, download queue,
cover hashes, search outcome log. It has historically been corrupted by stale
code paths that wrote a status dict missing one or more fields (see commits
e5074f5, 311b429 — the recurring "preserve cover_hashes" patches). Every such
event was discovered weeks later via a UI regression, not at write time.

This module gives the status cache a shape and a write-time check that LOGS
warnings (does not raise) when a save deviates from the schema. Goals:

1. **Document the shape.** A reader of this file knows what fields exist,
   which are required, what types they hold.
2. **Surface drift early.** When a code path writes a status missing `urls`
   or with a list where a dict is expected, a WARNING shows in logs.
3. **Never break a working app.** Validation is non-fatal by design; an
   unexpected field or type is logged but written anyway. We can tighten
   later once we trust the schema matches reality.

Not Pydantic — TypedDict + a hand-written `validate_status()` keep the
dependency surface flat (`requirements.txt` already has enough).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

try:
    from typing import TypedDict  # py3.8+
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore


# -- Sub-shapes -------------------------------------------------------------

class TrackEntry(TypedDict, total=False):
    """One row in to_download / have_locally / skipped_tracks."""
    artist: str
    title: str
    shazamed_at: Optional[int]   # epoch seconds; may be missing for legacy rows
    filepath: str                 # only on have_locally rows that matched a local file
    match_score: float            # only when fuzzy-matched
    soundeo_title: str            # the title resolved by Soundeo search


class FolderStat(TypedDict, total=False):
    folder: str
    file_count: int
    matched_count: int


class SearchOutcome(TypedDict, total=False):
    """One event in the search_outcomes log (event-sourced; replay rebuilds urls / not_found)."""
    t: str   # title
    a: str   # artist
    k: str   # canonical key (artist - title, normalized)
    u: str   # url if found, '' if not_found
    ts: int  # epoch seconds


class DownloadProgress(TypedDict, total=False):
    running: bool
    current_key: str
    current: int
    total: int
    queue: List[str]
    done: int


class ScanProgress(TypedDict, total=False):
    scanning: bool
    current: int
    total: int
    message: str


# -- Top-level status -------------------------------------------------------

class StatusCache(TypedDict, total=False):
    """Shape of `shazam_status_cache.json`. Every field is optional at the type
    level (total=False) because legacy / partial / freshly-bootstrapped status
    objects exist in the wild. `validate_status` distinguishes required vs
    optional at runtime."""

    # Counts (int)
    shazam_count: int
    local_count: int
    to_download_count: int

    # Track lists
    to_download: List[TrackEntry]
    have_locally: List[TrackEntry]
    skipped_tracks: List[TrackEntry]
    folder_stats: List[FolderStat]

    # Per-key dicts (variant-aware: same logical key may appear under multiple
    # normalized forms — see app.py:_set_url_variants / _set_cover_hash_variants).
    urls: Dict[str, str]
    starred: Dict[str, bool]
    soundeo_titles: Dict[str, str]
    soundeo_match_scores: Dict[str, float]
    track_ids: Dict[str, str]
    not_found: Dict[str, bool]
    dismissed: Dict[str, bool]
    dismissed_manual_check: List[str]
    cover_hashes: Dict[str, str]
    download_filepaths: Dict[str, str]
    # Local-only personal-curation flags (NEVER synced to Soundeo).
    # `maybe`: "I listened, I'm undecided — keep the track on my radar but
    # don't favorite it on Soundeo". Independent of star.
    # `listened`: "I've heard this track" — auto-set when audio plays past a
    # threshold, also manually toggleable.
    maybe: Dict[str, bool]
    listened: Dict[str, bool]

    # Event log — replay (newest per key wins) rebuilds urls + not_found.
    search_outcomes: List[SearchOutcome]

    # Transient runtime state (also persisted so UI restores after restart)
    download_progress: DownloadProgress
    scan_progress: ScanProgress
    download_queue: List[str]
    download_last_run: Dict[str, Any]
    compare_running: bool
    message: str
    error: str


# -- Required-vs-optional contract -----------------------------------------

# Fields that should always be present once status is initialized. Missing
# any of these post-bootstrap = a code path wrote a partial status.
STATUS_REQUIRED_KEYS: Set[str] = {
    "shazam_count",
    "to_download",
    "have_locally",
    "urls",
    "not_found",
    "search_outcomes",
}

# Fields that are valid but not always present.
STATUS_OPTIONAL_KEYS: Set[str] = {
    "local_count", "to_download_count", "skipped_tracks", "folder_stats",
    "starred", "soundeo_titles", "soundeo_match_scores", "track_ids",
    "dismissed", "dismissed_manual_check", "cover_hashes", "download_filepaths",
    "download_progress", "scan_progress", "download_queue", "download_last_run",
    "compare_running", "message", "error",
    "maybe", "listened",  # local-only curation flags (session 11, 2026-04-30)
}

# Expected runtime types per field (loose: lists are list, dicts are dict, etc.).
_EXPECTED_TYPES: Dict[str, type] = {
    "shazam_count": int,
    "local_count": int,
    "to_download_count": int,
    "to_download": list,
    "have_locally": list,
    "skipped_tracks": list,
    "folder_stats": list,
    "urls": dict,
    "starred": dict,
    "soundeo_titles": dict,
    "soundeo_match_scores": dict,
    "track_ids": dict,
    "not_found": dict,
    "dismissed": dict,
    "dismissed_manual_check": list,
    "cover_hashes": dict,
    "download_filepaths": dict,
    "search_outcomes": list,
    "download_progress": dict,
    "scan_progress": dict,
    "download_queue": list,
    "download_last_run": dict,
    "compare_running": bool,
    "message": str,
    "error": str,
    "maybe": dict,
    "listened": dict,
}


# -- Validator (non-fatal) -------------------------------------------------

def validate_status(status: Any, *, context: str = "") -> List[str]:
    """Validate a status dict against the schema. **Never raises.**

    Logs a single WARNING with all issues found. Returns the list of issue
    strings (empty if clean). Caller should write the status regardless —
    this is for visibility, not gating.

    Args:
        status: the dict about to be written / just loaded.
        context: optional caller hint (e.g. "save_status_cache",
                 "rebuild_from_search_log") so the log line is traceable.
    """
    issues: List[str] = []

    if status is None:
        issues.append("status is None")
        _log(issues, context)
        return issues

    if not isinstance(status, dict):
        issues.append(f"status is {type(status).__name__}, expected dict")
        _log(issues, context)
        return issues

    keys = set(status.keys())

    # Required keys
    missing = STATUS_REQUIRED_KEYS - keys
    if missing:
        issues.append(f"missing required keys: {sorted(missing)}")

    # Unknown keys — don't fail, but flag once so we can decide whether to
    # add them to the schema or remove them from the writer.
    known = STATUS_REQUIRED_KEYS | STATUS_OPTIONAL_KEYS
    unknown = keys - known
    if unknown:
        issues.append(f"unknown keys: {sorted(unknown)}")

    # Type checks (only for keys that ARE present; absent = covered above)
    for k, expected in _EXPECTED_TYPES.items():
        if k not in status:
            continue
        v = status[k]
        if v is None:
            # We allow None for any field rather than being noisy on absence.
            continue
        if not isinstance(v, expected):
            issues.append(
                f"{k}: type {type(v).__name__}, expected {expected.__name__}"
            )

    # Specific cross-field invariants (cheap to check, catch real bugs)
    sc = status.get("shazam_count")
    if isinstance(sc, int) and sc < 0:
        issues.append(f"shazam_count is negative: {sc}")

    td = status.get("to_download")
    if isinstance(td, list):
        for i, t in enumerate(td[:10]):  # sample first 10 — cheap
            if not isinstance(t, dict):
                issues.append(f"to_download[{i}] not a dict: {type(t).__name__}")
                break
            if "artist" not in t or "title" not in t:
                issues.append(f"to_download[{i}] missing artist/title")
                break

    _log(issues, context)
    return issues


def _log(issues: List[str], context: str) -> None:
    if not issues:
        return
    prefix = f"[{context}] " if context else ""
    logging.warning(
        "%sstatus schema check: %d issue(s): %s",
        prefix, len(issues), "; ".join(issues),
    )
