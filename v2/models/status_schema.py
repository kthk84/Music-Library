"""TypedDict schema + non-fatal validator for `shazam_status_cache.json`.

`save_status_cache` runs `validate_status(status)` on every write. Issues are
logged at WARNING; the write proceeds regardless. Goal is visibility, not gating.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set, TypedDict

# --- TypedDict (documentation; runtime check is below) ----------------------

class StatusCache(TypedDict, total=False):
    # Required scope counts
    shazam_count: int
    local_count: int
    to_download_count: int
    # Required track lists
    to_download: List[Dict[str, Any]]
    have_locally: List[Dict[str, Any]]
    skipped_tracks: List[Dict[str, Any]]
    folder_stats: List[Dict[str, Any]]
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
    maybe: Dict[str, bool]
    listened: Dict[str, bool]
    # Event log (search outcomes — single source of truth for url / not_found state)
    search_outcomes: List[Dict[str, Any]]
    # Transient runtime state — only present when active
    download_progress: Dict[str, Any]
    scan_progress: Dict[str, Any]
    download_queue: List[str]
    download_last_run: Dict[str, Any]
    compare_running: bool
    message: str
    error: str


# --- Runtime validation -----------------------------------------------------

# Keys that should always be present in a healthy status. Missing → warn.
STATUS_REQUIRED_KEYS: Set[str] = {
    "shazam_count", "to_download", "have_locally", "urls", "not_found", "search_outcomes",
}

# Fields that are valid but not always present.
STATUS_OPTIONAL_KEYS: Set[str] = {
    "local_count", "to_download_count", "skipped_tracks", "folder_stats",
    "starred", "soundeo_titles", "soundeo_match_scores", "track_ids",
    "dismissed", "dismissed_manual_check", "cover_hashes", "download_filepaths",
    "download_progress", "scan_progress", "download_queue", "download_last_run",
    "compare_running", "message", "error",
    "maybe", "listened",
}

# Type expectations for each known field.
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


def validate_status(status: Dict[str, Any], context: str = "save") -> List[str]:
    """Non-fatal schema check. Logs WARNING on issues, returns list of issue strings.

    Never raises. The caller (save_status_cache) writes the file regardless.
    """
    issues: List[str] = []
    if not isinstance(status, dict):
        issues.append(f"status is not a dict (got {type(status).__name__})")
        logging.warning("[%s] status schema check: %s", context, issues[0])
        return issues
    # Missing required keys
    missing = sorted(STATUS_REQUIRED_KEYS - set(status.keys()))
    if missing:
        issues.append(f"missing required keys: {missing}")
    # Type checks
    for key, value in status.items():
        expected = _EXPECTED_TYPES.get(key)
        if expected is None:
            # Unknown key — soft warning
            issues.append(f"unknown key '{key}' (not in schema)")
            continue
        if not isinstance(value, expected):
            issues.append(f"{key}: type {type(value).__name__}, expected {expected.__name__}")
    # Sanity: shazam_count non-negative
    sc = status.get("shazam_count")
    if isinstance(sc, int) and sc < 0:
        issues.append(f"shazam_count is negative: {sc}")
    # Track items basic shape
    for list_key in ("to_download", "have_locally", "skipped_tracks"):
        items = status.get(list_key)
        if isinstance(items, list):
            for i, t in enumerate(items[:5]):  # cap inspection cost
                if not isinstance(t, dict):
                    issues.append(f"{list_key}[{i}]: not a dict ({type(t).__name__})")
                    continue
                if not t.get("artist") or not t.get("title"):
                    issues.append(f"{list_key}[{i}]: missing artist/title")
                    break
    if issues:
        logging.warning(
            "[%s] status schema check: %d issue(s): %s",
            context, len(issues), "; ".join(issues),
        )
    return issues
