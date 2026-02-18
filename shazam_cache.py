"""
Persistent caches for Shazam sync.
- Shazam tracks: saved list from Shazam DB, merged with new fetches (no duplicates)
- Local scan: cached folder scan result to avoid re-scanning every Compare
"""
import json
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SHAZAM_CACHE_PATH = os.path.join(_PROJECT_ROOT, "shazam_cache.json")
LOCAL_SCAN_CACHE_PATH = os.path.join(_PROJECT_ROOT, "local_scan_cache.json")


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _save_json_atomic(path: str, data: Any) -> None:
    """Write to temp file then rename for atomicity. Flush+fsync so data is on disk before rename."""
    tmp = path + ".tmp." + str(os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _track_key(t: Dict) -> tuple:
    return (str(t.get("artist", "")).strip().lower(), str(t.get("title", "")).strip().lower())


# --- Shazam cache ---


def load_shazam_cache() -> List[Dict]:
    """Load cached Shazam tracks. Returns list of {artist, title, shazamed_at?}."""
    data = _load_json(SHAZAM_CACHE_PATH, {"tracks": []})
    return data.get("tracks", [])


def save_shazam_cache(tracks: List[Dict]) -> None:
    """Save Shazam tracks to cache."""
    _save_json(SHAZAM_CACHE_PATH, {
        "tracks": tracks,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })


def merge_shazam_tracks(existing: List[Dict], new: List[Dict]) -> tuple:
    """
    Merge new Shazam tracks into existing. No duplicates (by artist+title).
    New tracks are appended. For duplicates, prefer new (updated shazamed_at).
    Returns (merged_list, added_count).
    """
    by_key: Dict[tuple, Dict] = {_track_key(t): t for t in existing}
    added = 0
    for t in new:
        k = _track_key(t)
        if k not in by_key:
            added += 1
        by_key[k] = t  # prefer new (may have newer shazamed_at)
    # Order: new first (newest Shazam order), then existing-only
    result = []
    seen = set()
    for t in new:
        k = _track_key(t)
        if k not in seen:
            result.append(by_key[k])
            seen.add(k)
    for t in existing:
        k = _track_key(t)
        if k not in seen:
            result.append(by_key[k])
            seen.add(k)
    return result, added


# --- Local scan cache (v2: incremental + per-folder) ---

_LOCAL_SCAN_VERSION = 3  # v3: tag-based scan (was v2 filename-only)


def load_local_scan_cache() -> Optional[Dict]:
    """
    Load cached local scan. v2: {version, folders, files: {path: {mtime, size, artist, title}}, tracks}.
    Also returns 'tracks' list for backward compat (derived from files).
    """
    data = _load_json(LOCAL_SCAN_CACHE_PATH, None)
    if not data or not isinstance(data, dict):
        return None
    return data


def save_local_scan_cache(folder_paths: List[str], tracks: List[Dict], files_cache: Optional[Dict] = None) -> None:
    """Save local scan result. files_cache: {path: {mtime, size, artist, title}} for incremental."""
    flat = [{"artist": t.get("artist", ""), "title": t.get("title", ""), "filepath": t.get("filepath", "")} for t in tracks]
    folders_norm = [os.path.abspath(str(p)).rstrip(os.sep) for p in folder_paths if p]
    payload = {
        "version": _LOCAL_SCAN_VERSION,
        "folders": folders_norm,
        "tracks": flat,
        "scanned_at": datetime.utcnow().isoformat() + "Z",
    }
    if files_cache is not None:
        payload["files"] = files_cache
    _save_json(LOCAL_SCAN_CACHE_PATH, payload)


def local_scan_cache_valid(cache: Optional[Dict], folder_paths: List[str]) -> bool:
    """True if cache exists, folder list matches, version current, and cache has tracks."""
    if not cache or not folder_paths:
        return False
    if cache.get("version") != _LOCAL_SCAN_VERSION:
        return False
    cached = set(os.path.abspath(str(f)).rstrip(os.sep) for f in cache.get("folders", []))
    requested = set(os.path.abspath(str(p)).rstrip(os.sep) for p in folder_paths if p)
    if cached != requested:
        return False
    tracks = cache.get("tracks", [])
    if not tracks:
        return False
    return True


def get_folders_to_scan(cache: Optional[Dict], folder_paths: List[str]) -> tuple:
    """
    Per-folder cache: return (folders_to_scan, cached_files_by_path).
    folders_to_scan = new folders not in cache. Scan only these.
    cached_files_by_path = files from cache for paths under folders we're keeping.
    """
    requested = set(os.path.abspath(p).rstrip(os.sep) for p in folder_paths if p)
    if not requested:
        return [], {}

    if not cache or cache.get("version") != _LOCAL_SCAN_VERSION:
        return list(requested), {}

    cached_folders = {os.path.abspath(f).rstrip(os.sep) for f in cache.get("folders", [])}
    files = cache.get("files") or {}

    new_folders = requested - cached_folders
    kept_folders = requested & cached_folders

    # Filter cache to paths under kept folders only
    def _under(path: str, folders: set) -> bool:
        path_abs = os.path.abspath(path)
        for d in folders:
            if path_abs == d or path_abs.startswith(d + os.sep):
                return True
        return False

    cached_for_merge = {p: m for p, m in files.items() if _under(p, kept_folders)}
    to_scan = list(new_folders)
    return to_scan, cached_for_merge


# --- Skip list (tracks user chose to skip from sync) ---

SKIP_LIST_PATH = os.path.join(_PROJECT_ROOT, "shazam_skip_list.json")


def load_skip_list() -> set:
    """Load skipped (artist, title) keys as set of tuples."""
    data = _load_json(SKIP_LIST_PATH, {"keys": []})
    keys = data.get("keys", [])
    return set(tuple(k) for k in keys if isinstance(k, list) and len(k) >= 2)


def save_skip_list(skip_set: set) -> None:
    """Persist skip list."""
    keys = [list(k) for k in skip_set]
    _save_json(SKIP_LIST_PATH, {"keys": keys})


def add_to_skip_list(tracks: List[Dict]) -> int:
    """Add tracks to skip list. Returns count added."""
    skip = load_skip_list()
    before = len(skip)
    for t in tracks:
        skip.add(_track_key(t))
    save_skip_list(skip)
    return len(skip) - before


def remove_from_skip_list(tracks: List[Dict]) -> int:
    """Remove tracks from skip list. Returns count removed."""
    skip = load_skip_list()
    before = len(skip)
    for t in tracks:
        skip.discard(_track_key(t))
    save_skip_list(skip)
    return before - len(skip)


# --- App state (MP3 Cleaner: last folder, etc. – restore on load/refresh) ---

APP_STATE_PATH = os.path.join(_PROJECT_ROOT, "app_state.json")


def load_app_state() -> Dict:
    """Load app-wide state (last folder path, etc.) for restore on load/refresh."""
    return _load_json(APP_STATE_PATH, {})


def save_app_state(state: Dict) -> None:
    """Persist app state after any relevant interaction."""
    if not state:
        return
    existing = load_app_state()
    existing.update(state)
    _save_json(APP_STATE_PATH, existing)


# --- Last compare status (for instant restore on page load) ---

STATUS_CACHE_PATH = os.path.join(_PROJECT_ROOT, "shazam_status_cache.json")


def load_status_cache() -> Optional[Dict]:
    """Load last compare result for instant display."""
    return _load_json(STATUS_CACHE_PATH, None)


def save_status_cache(status: Dict) -> None:
    """Persist compare result. Uses atomic write to prevent corrupt reads on refresh."""
    _save_json_atomic(STATUS_CACHE_PATH, status)


# --- Mutation log ---
MUTATION_LOG_PATH = os.path.join(_PROJECT_ROOT, "shazam_mutation_log.json")
_MAX_MUTATION_LOG_ENTRIES = 2000


def load_mutation_log() -> List[Dict]:
    return _load_json(MUTATION_LOG_PATH, [])


def append_mutations(entries: List[Dict]) -> None:
    """Append mutation entries and trim to keep the log bounded."""
    if not entries:
        return
    log = load_mutation_log()
    log.extend(entries)
    if len(log) > _MAX_MUTATION_LOG_ENTRIES:
        log = log[-_MAX_MUTATION_LOG_ENTRIES:]
    _save_json_atomic(MUTATION_LOG_PATH, log)


def log_starred_mutations(
    newly_starred: List[str],
    newly_unstarred: List[str],
    source: str = "crawl",
) -> List[Dict]:
    """Build mutation entries for starred/unstarred changes, persist, and return them."""
    ts = datetime.utcnow().isoformat() + "Z"
    entries = []
    for key in newly_starred:
        entries.append({"timestamp": ts, "action": "starred", "key": key, "source": source})
    for key in newly_unstarred:
        entries.append({"timestamp": ts, "action": "unstarred", "key": key, "source": source})
    append_mutations(entries)
    return entries
