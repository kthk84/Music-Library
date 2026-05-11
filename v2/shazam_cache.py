"""
Persistent caches for Shazam sync.
- Shazam tracks: saved list from Shazam DB, merged with new fetches (no duplicates)
- Local scan: cached folder scan result to avoid re-scanning every Compare
When running from a frozen bundle, data lives in ~/Library/Application Support/SoundBridge.
"""
import json
import os
import shutil
import threading
from typing import List, Dict, Any, Optional
from datetime import datetime

from app_paths import get_project_root_for_data

_PROJECT_ROOT = get_project_root_for_data(__file__)
SHAZAM_CACHE_PATH = os.path.join(_PROJECT_ROOT, "shazam_cache.json")
LOCAL_SCAN_CACHE_PATH = os.path.join(_PROJECT_ROOT, "local_scan_cache.json")

# Serialize status cache reads/writes across threads to avoid lost updates
# (download worker vs search/star/compare background jobs).
_STATUS_CACHE_LOCK = threading.Lock()


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
    """Write to temp file then rename for atomicity. Flush+fsync so data is on disk before rename.
    Falls back to a direct write if the atomic rename times out (e.g. transient macOS filesystem issue)."""
    import logging as _log
    tmp = path + ".tmp." + str(os.getpid())
    renamed = False
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                logging.debug("silent except at shazam_cache.py:53", exc_info=True)
        os.replace(tmp, path)
        renamed = True
    except Exception as _e:
        if not renamed:
            _log.warning('_save_json_atomic: atomic rename failed (%s) — falling back to direct write for %s', _e, os.path.basename(path))
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        logging.debug("silent except at shazam_cache.py:66", exc_info=True)
            except Exception as _e2:
                _log.error('_save_json_atomic: direct write also failed for %s: %s', os.path.basename(path), _e2)
                raise
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                logging.debug("silent except at shazam_cache.py:75", exc_info=True)


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
    Merge new Shazam tracks into existing. No duplicates by (artist, title).

    For duplicates: keep newest `shazamed_at`, increment `shazamed_count` when
    the new timestamp differs from the stored one (i.e. the user Shazammed the
    same track again at a different moment). Tracks without `shazamed_count`
    default to 1. This is how the multi-shazam UI flag is sourced.

    Returns (merged_list, added_count).
    """
    # Seed merge map with existing tracks; ensure shazamed_count is present.
    by_key: Dict[tuple, Dict] = {}
    for t in existing:
        k = _track_key(t)
        merged = dict(t)
        if 'shazamed_count' not in merged or not isinstance(merged.get('shazamed_count'), int):
            merged['shazamed_count'] = 1
        by_key[k] = merged

    added = 0
    for t in new:
        k = _track_key(t)
        if k not in by_key:
            new_entry = dict(t)
            new_entry.setdefault('shazamed_count', 1)
            by_key[k] = new_entry
            added += 1
            continue
        # Duplicate: only bump count when the timestamp actually moved (user
        # Shazammed it again at a different moment). Re-fetching the same
        # library snapshot must NOT inflate the count.
        existing_entry = by_key[k]
        old_ts = existing_entry.get('shazamed_at')
        new_ts = t.get('shazamed_at')
        if new_ts and old_ts != new_ts:
            existing_entry['shazamed_count'] = int(existing_entry.get('shazamed_count', 1)) + 1
            existing_entry['shazamed_at'] = new_ts
        elif new_ts and not old_ts:
            existing_entry['shazamed_at'] = new_ts
        # Carry forward any other fields from the new entry that aren't already set
        for fk, fv in t.items():
            if fk in ('artist', 'title', 'shazamed_at', 'shazamed_count'):
                continue
            if fk not in existing_entry:
                existing_entry[fk] = fv

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
    """True if cache exists, folder list matches, version current, has tracks, and no folder was modified after scan (so new downloads are seen)."""
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
    scanned_at = cache.get("scanned_at")
    if scanned_at:
        try:
            scan_ts = datetime.fromisoformat(scanned_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            scan_ts = 0
        for folder in requested:
            if folder and os.path.isdir(folder):
                try:
                    if os.path.getmtime(folder) > scan_ts:
                        return False
                except OSError:
                    logging.debug("silent except at shazam_cache.py:184", exc_info=True)
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


# --- App state (SoundBridge: last folder, etc. – restore on load/refresh) ---

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
    """Load last compare result for instant display. If main file has track lists but no search_outcomes/urls, merge in from .bak so dots (purple/orange) never disappear."""
    with _STATUS_CACHE_LOCK:
        status = _load_json(STATUS_CACHE_PATH, None)
        if status is not None and not status.get("search_outcomes"):
            _old_log_path = os.path.join(_PROJECT_ROOT, "shazam_search_log.json")
            if os.path.exists(_old_log_path):
                old_log = _load_json(_old_log_path, [])
                if old_log:
                    status = dict(status)
                    status["search_outcomes"] = old_log
                    save_status_cache(status)
                    try:
                        os.remove(_old_log_path)
                    except OSError:
                        logging.debug("silent except at shazam_cache.py:297", exc_info=True)
        if status is not None and (status.get("to_download") or status.get("have_locally")) and not (status.get("search_outcomes") or status.get("urls")):
            bak = _load_json(STATUS_CACHE_PATH + ".bak", None)
            if bak and isinstance(bak, dict) and (bak.get("search_outcomes") or bak.get("urls")):
                if bak.get("search_outcomes"):
                    status = dict(status)
                    status["search_outcomes"] = list(bak["search_outcomes"])
                    status["urls"], status["not_found"] = _replay_search_outcomes(status["search_outcomes"], status.get("urls"))
                else:
                    status = dict(status)
                    status["urls"] = dict(bak.get("urls") or {})
                    urls = status["urls"]
                    nf = dict(bak.get("not_found") or {})
                    status["not_found"] = {k: v for k, v in nf.items() if not (urls.get(k) or (isinstance(k, str) and urls.get(k.lower())))}
                for key in ("track_ids", "starred", "soundeo_titles", "soundeo_match_scores", "dismissed_manual_check"):
                    if bak.get(key) and not status.get(key):
                        v = bak[key]
                        status[key] = list(v) if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v)
        return status


def save_status_cache(status: Dict) -> None:
    """Persist compare result. Uses atomic write to prevent corrupt reads on refresh.
    Paper trail: never persist not_found for a track that has a URL (so status stays correct).
    When search_outcomes exists, urls/not_found are derived from it so batch search data is always consistent.
    Never wipe search_outcomes: if status has none, preserve from existing file, then from .bak, so dots never disappear."""
    with _STATUS_CACHE_LOCK:
        out = dict(status)
    # Guard against lost updates: long-running jobs may save an older snapshot of status after
    # downloads finished. Keep any existing have_locally entries (with filepath) and ensure those
    # tracks are not written back into to_download.
    try:
        existing_for_merge = _load_json(STATUS_CACHE_PATH, None)
        if existing_for_merge and isinstance(existing_for_merge, dict):
            def _k(t: Dict) -> tuple:
                return (str(t.get("artist", "")).strip().lower(), str(t.get("title", "")).strip().lower())

            have_now = list(out.get("have_locally") or [])
            have_keys = {_k(h) for h in have_now if isinstance(h, dict)}

            existing_have = [h for h in (existing_for_merge.get("have_locally") or []) if isinstance(h, dict) and h.get("filepath")]
            for h in existing_have:
                kk = _k(h)
                if kk not in have_keys:
                    have_now.append(h)
                    have_keys.add(kk)
            out["have_locally"] = have_now

            to_dl_now = [t for t in (out.get("to_download") or []) if isinstance(t, dict) and _k(t) not in have_keys]
            out["to_download"] = to_dl_now
            out["to_download_count"] = len(to_dl_now)
    except Exception:
        logging.debug("silent except at shazam_cache.py:349", exc_info=True)

    # Must run on every save (not only when the merge try/except fails): replay + atomic write.
    log = out.get('search_outcomes') or []
    if not log:
        existing = load_status_cache()
        if existing:
            existing_log = (existing.get('search_outcomes') or [])[:]
            if existing_log:
                out['search_outcomes'] = existing_log
                log = existing_log
        if not log:
            bak = _load_json(STATUS_CACHE_PATH + ".bak", None)
            if bak and isinstance(bak, dict) and (bak.get('search_outcomes') or []):
                out['search_outcomes'] = list(bak['search_outcomes'])
                log = out['search_outcomes']
                if bak.get('track_ids'):
                    out.setdefault('track_ids', {}).update(bak.get('track_ids') or {})
                if bak.get('starred'):
                    out.setdefault('starred', {}).update(bak.get('starred') or {})
                if bak.get('soundeo_titles'):
                    out.setdefault('soundeo_titles', {}).update(bak.get('soundeo_titles') or {})
    if log:
        urls, nf = _replay_search_outcomes(log, out.get('urls'))
        out['urls'] = urls
        out['not_found'] = nf
    urls = out.get('urls') or {}
    nf = out.get('not_found') or {}
    out['not_found'] = {k: v for k, v in nf.items() if not (urls.get(k) or (isinstance(k, str) and urls.get(k.lower())))}
    # v2: schema check before write — non-fatal. Logs WARNING with all issues
    # found (missing required keys, wrong types). We still write the file even
    # if there are issues; this is for visibility, not gating.
    try:
        from models import validate_status
        validate_status(out, context="save_status_cache")
    except ImportError:
        # models package not available (running v1 paths or partial install) —
        # fall through silently. Schema check is best-effort.
        pass
    except Exception:
        import logging as _log
        _log.exception("save_status_cache: validate_status crashed (continuing write)")
    # Keep one backup of previous version so status/context per track can be restored if lost
    if os.path.exists(STATUS_CACHE_PATH) and os.path.getsize(STATUS_CACHE_PATH) > 0:
        try:
            shutil.copy2(STATUS_CACHE_PATH, STATUS_CACHE_PATH + ".bak")
        except OSError as _e:
            import logging as _log
            _log.warning("save_status_cache: backup copy failed: %s", _e)
    _save_json_atomic(STATUS_CACHE_PATH, out)


# --- Search outcomes: stored inside status cache (single source of truth) ---
_MAX_SEARCH_OUTCOMES = 100_000


def _strip_all_parens(key: str) -> str:
    """Remove all (...) and [...] segments, collapse spaces. Mirrors app._strip_all_parens."""
    import re
    s = (key or "").strip()
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or key or ""


def _deep_norm_key(key: str) -> str:
    """Deep normalize: strip parens, lowercase, unify '&'/',' separators, sort artists. Mirrors app._deep_norm_key."""
    import unicodedata
    s = _strip_all_parens(key)
    s = ''.join(ch for ch in unicodedata.normalize('NFKD', s) if not unicodedata.combining(ch))
    s = s.lower().replace(' & ', ', ')
    if ' - ' in s:
        artist_part, title_part = s.split(' - ', 1)
        artists = sorted(a.strip() for a in artist_part.split(', ') if a.strip())
        s = ', '.join(artists) + ' - ' + title_part
    return s


def _url_key_variants(key: str) -> List[str]:
    """Key variants used for stable URL persistence across refresh/rebuilds."""
    if not key or not isinstance(key, str):
        return []
    k = key.strip()
    if not k:
        return []
    norm = _strip_all_parens(k).strip()
    deep = _deep_norm_key(k)
    out = [
        k,
        k.lower(),
        norm,
        norm.lower(),
        deep,
        deep.lower(),
    ]
    # dot-stripped variants help match 'R.E.Zarin' style keys
    out += [
        norm.replace('.', ''),
        norm.replace('.', '').lower(),
        deep.replace('.', ''),
        deep.replace('.', '').lower(),
    ]
    # Deduplicate while preserving order
    seen = set()
    out2 = []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            out2.append(v)
    return out2


def _replay_search_outcomes(log: List[Dict], existing_urls: Optional[Dict] = None) -> tuple:
    """Replay search_outcomes list (newest per key wins). Returns (urls, not_found). Merge in existing_urls for keys not in log (e.g. sync-origin)."""
    urls = {}
    not_found = {}
    for e in log:
        key = e.get("k")
        if not key or not isinstance(key, str):
            continue
        action = e.get("a")
        if action == "f":
            url = e.get("u")
            if url:
                for vk in _url_key_variants(key):
                    urls[vk] = url
                not_found.pop(key, None)
                not_found.pop(key.lower(), None)
        elif action == "n":
            not_found[key] = True
            not_found[key.lower()] = True
    not_found = {k: v for k, v in not_found.items() if not (urls.get(k) or (isinstance(k, str) and urls.get(k.lower())))}
    for k, v in (existing_urls or {}).items():
        if isinstance(k, str) and k not in urls:
            urls[k] = v
    return (urls, not_found)


def get_urls_and_not_found_from_log(existing_status: Optional[Dict] = None) -> tuple:
    """Return (urls, not_found) from status['search_outcomes'] in the same file — single source of truth. If no outcomes, returns existing_status urls/not_found."""
    e = existing_status or {}
    log = e.get("search_outcomes") or []
    if not log:
        return (dict(e.get("urls") or {}), dict(e.get("not_found") or {}))
    return _replay_search_outcomes(log, e.get("urls"))


def log_search_outcome(key: str, found: bool, url: Optional[str] = None, status_to_update: Optional[Dict] = None) -> None:
    """Append one search outcome. If status_to_update is provided, append to it and derive urls/not_found on it (caller saves). Otherwise load status cache, append, save — single source of truth in that file."""
    from datetime import datetime
    entry = {"t": datetime.utcnow().isoformat() + "Z", "a": "f" if found else "n", "k": key}
    if found and url:
        entry["u"] = url
    log_search_outcomes_batch([entry], status_to_update)


def log_search_outcomes_batch(entries: List[Dict], status_to_update: Optional[Dict] = None) -> None:
    """Append multiple search outcomes (each: t, a, k, u?). If status_to_update provided, caller saves. Otherwise load, append, save."""
    if not entries:
        return
    status = status_to_update if status_to_update is not None else (load_status_cache() or {})
    status.setdefault("search_outcomes", [])
    status["search_outcomes"] = (status.get("search_outcomes") or []) + list(entries)
    if len(status["search_outcomes"]) > _MAX_SEARCH_OUTCOMES:
        status["search_outcomes"] = status["search_outcomes"][-_MAX_SEARCH_OUTCOMES:]
    status["urls"], status["not_found"] = _replay_search_outcomes(status["search_outcomes"], status.get("urls"))
    if status_to_update is None:
        save_status_cache(status)


def rebuild_status_from_search_log(existing_status: Optional[Dict] = None) -> Dict:
    """Replay search_outcomes (from status cache) to rebuild urls and not_found. Use for recovery. Single source of truth = status cache only."""
    status = existing_status or load_status_cache() or {}
    log = status.get("search_outcomes") or []
    urls, not_found = _replay_search_outcomes(log, status.get("urls"))
    out = dict(status)
    out["urls"] = urls
    out["not_found"] = not_found
    return out


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
