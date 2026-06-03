"""Cover art helpers — extracted from app.py in session 9 (2026-04-29).

Architecture (see docs/COVER_ART_ARCHITECTURE.md):
The on-disk ``cover_cache/`` directory is the single source of truth. Every
cover lives at ``cover_cache/<md5(track_key_variant)>.jpg`` — the filename is a
deterministic hash of the track key, so the key→file mapping is always
reconstructable from disk and a track key. ``cover_hashes`` (key→hash) is
therefore *derived* state, never authoritative; the server recomputes it from
disk on every status read so it can never go sparse. ``cover_key_variants`` is
the ONE canonical normalizer used by storage, lookup and disk-derivation, so a
cover cached under any variant is found under all of them.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from typing import Dict, List, Optional, Tuple

import requests

from app_paths import get_project_root_for_data
from .keys import _strip_all_parens, _deep_norm_key

# Cover files we recognise on disk, in priority order, with their MIME types.
_COVER_EXTS: Tuple[Tuple[str, str], ...] = (
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".png", "image/png"),
)

# Memoized set of cover-file hash-stems on disk. Listing a ~10k-file directory
# on every /status poll (≈1 Hz) is wasteful; cache it per directory, keyed on a
# cheap signature (mtime_ns + size) that changes whenever a cover is added or
# removed. Keyed by directory PATH so distinct dirs (e.g. per-test tmp dirs)
# never share a cache entry.
_DISK_HASHSET_CACHE: Dict[str, Dict] = {}
_DISK_HASHSET_LOCK = threading.Lock()


def _get_cover_cache_dir() -> str:
    """Return (and create) the directory for locally cached cover art images."""
    base = get_project_root_for_data(__file__)
    path = os.path.join(base, "cover_cache")
    os.makedirs(path, exist_ok=True)
    return path


def cover_key_variants(key: str) -> List[str]:
    """Canonical, ordered, de-duplicated list of key variants for a track key.

    This is the SINGLE SOURCE OF TRUTH for cover-key normalization. Storage
    (`_set_cover_hash_variants`), on-demand resolution (`find_cover_file_for_key`)
    and disk-derivation (`compute_cover_hashes_from_disk`) all go through it, so
    a cover cached under any one variant is always resolvable under the others.
    Historically these three code paths each rolled their own variant logic and
    drifted apart — that mismatch was a recurring source of "blank cover even
    though the file exists" bugs.

    Order: exact, lowercase, parens-stripped (+lc), deep-normalized (+lc).
    """
    out: List[str] = []
    seen = set()

    def _add(v: Optional[str]) -> None:
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    k = (key or "").strip()
    if not k:
        return out
    _add(k)
    _add(k.lower())
    try:
        norm = _strip_all_parens(k).strip()
        if norm:
            _add(norm)
            _add(norm.lower())
    except Exception:
        logging.debug("cover_key_variants: strip-parens variant failed", exc_info=True)
    try:
        deep = _deep_norm_key(k)
        if deep:
            _add(deep)
            _add(deep.lower())
    except Exception:
        logging.debug("cover_key_variants: deep-norm variant failed", exc_info=True)
    return out


def _cover_dir_signature(cover_dir: str) -> Optional[Tuple[int, int]]:
    """Cheap directory signature for memoization — changes on any add/remove."""
    try:
        st = os.stat(cover_dir)
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))), int(st.st_size))
    except OSError:
        return None


def _disk_hash_set(cover_dir: str) -> frozenset:
    """Memoized set of cover-file hash-stems present on disk (single listdir)."""
    sig = _cover_dir_signature(cover_dir)
    entry = _DISK_HASHSET_CACHE.get(cover_dir)
    cached_hashes = (entry or {}).get("hashes") or frozenset()
    if sig is not None and entry is not None and entry.get("sig") == sig:
        return cached_hashes
    hashes = set()
    try:
        for fn in os.listdir(cover_dir):
            low = fn.lower()
            if low.endswith(".jpg") or low.endswith(".jpeg") or low.endswith(".png"):
                hashes.add(fn.rsplit(".", 1)[0].lower())
    except OSError as e:
        logging.warning("_disk_hash_set: listdir failed: %s", e)
        return cached_hashes
    frozen = frozenset(hashes)
    with _DISK_HASHSET_LOCK:
        _DISK_HASHSET_CACHE[cover_dir] = {"sig": sig, "hashes": frozen}
    return frozen


def find_cover_file_for_key(key: str) -> Optional[Tuple[str, str]]:
    """Resolve a track key to an on-disk cover file, trying every canonical variant.

    Returns ``(filepath, mimetype)`` or ``None``. This is what the by-key cover
    endpoint uses: the frontend passes the track key it already has, and the
    server resolves it to a file — so a cover renders whenever the file exists,
    independent of any persisted ``cover_hashes`` map. The md5 output is hex, so
    the constructed path is always inside ``cover_cache`` (no traversal risk
    regardless of the key's contents). Stats directly (≤6 variants) rather than
    consulting the memoized set, so a just-cached file is served immediately.
    """
    if not key:
        return None
    cover_dir = _get_cover_cache_dir()
    for v in cover_key_variants(key):
        h = hashlib.md5(v.encode("utf-8")).hexdigest()
        for ext, mime in _COVER_EXTS:
            fp = os.path.join(cover_dir, h + ext)
            if os.path.isfile(fp):
                return (fp, mime)
    return None


def compute_cover_hashes_from_disk(status: Dict) -> Dict[str, str]:
    """Return a COMPLETE, disk-accurate ``cover_hashes`` map for ``status``.

    Because covers are named ``<md5(key_variant)>.jpg``, the entire map is
    reconstructable from the directory contents + the track keys in ``status``.
    Computing it fresh on every status read makes the served map impossible to
    be sparser than what is actually on disk — which is the permanent fix for
    the recurring "covers vanished after an interruption / status rebuild" bug:
    persistence races can no longer affect what the UI sees.

    Each found cover is mapped under the row's primary key ``"Artist - Title"``
    (and lowercase) so the overview render always hits, AND under the matched
    variant so the playbar / older lookups hit. Non-mutating; uses the memoized
    disk set so it is cheap to call at ~1 Hz.
    """
    if not isinstance(status, dict):
        return {}
    cover_dir = _get_cover_cache_dir()
    diskset = _disk_hash_set(cover_dir)
    if not diskset:
        return {}
    soundeo_titles = status.get("soundeo_titles") or {}
    result: Dict[str, str] = {}

    def _map_primary(primary: str, extra_candidates: Tuple[str, ...] = ()) -> None:
        if not isinstance(primary, str) or not primary.strip() or primary in result:
            return
        candidates: List[str] = []
        for base in (primary, *extra_candidates):
            candidates.extend(cover_key_variants(base))
        seen = set()
        for v in candidates:
            if v in seen:
                continue
            seen.add(v)
            h = hashlib.md5(v.encode("utf-8")).hexdigest()
            if h in diskset:
                result[primary] = h
                result[primary.lower()] = h
                result[v] = h  # matched variant too, for playbar/back-compat lookups
                return

    for k in (status.get("urls") or {}):
        _map_primary(k)
    for k in (status.get("starred") or {}):
        _map_primary(k)
    for src in ("have_locally", "to_download", "skipped_tracks", "maybe", "listened"):
        for t in (status.get(src) or []):
            if not isinstance(t, dict):
                continue
            a, ti = t.get("artist"), t.get("title")
            if not a or not ti:
                continue
            base = f"{a} - {ti}"
            sd = soundeo_titles.get(base) or soundeo_titles.get(base.lower())
            extra = (f"{a} - {sd}",) if isinstance(sd, str) and sd else ()
            _map_primary(base, extra)
    return result


def _cache_cover_art(key: str, cover_url: str) -> Optional[str]:
    """Download cover art from `cover_url` and cache it locally."""
    if not cover_url or not cover_url.startswith("http"):
        return None
    key_hash = hashlib.md5(key.encode("utf-8")).hexdigest()
    cover_path = os.path.join(_get_cover_cache_dir(), key_hash + ".jpg")
    if os.path.exists(cover_path):
        return key_hash
    try:
        resp = requests.get(cover_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://soundeo.com/",
        })
        if resp.status_code == 200 and resp.content:
            with open(cover_path, "wb") as f:
                f.write(resp.content)
            return key_hash
    except Exception as e:
        logging.warning("_cache_cover_art: failed to download %s — %s", cover_url, e)
    return None


def _set_cover_hash_variants(status: dict, key: str, cover_hash: str) -> None:
    """Store cover hash under every canonical key variant so UI lookups always hit.

    Uses the shared `cover_key_variants` normalizer (same as lookup/disk-derive)
    so storage and resolution can never drift apart.
    """
    if not status or not key or not cover_hash:
        return
    bucket = status.setdefault('cover_hashes', {})
    for v in cover_key_variants(key):
        if v not in bucket:
            bucket[v] = cover_hash


def _cover_hashes_for_status(status: dict) -> dict:
    """Return cover_hashes dict filtered to entries whose file actually exists."""
    cover_dir = _get_cover_cache_dir()
    cover_hashes: Dict[str, str] = {}
    existing = status.get("cover_hashes") or {}
    for key, key_hash in existing.items():
        cover_path = os.path.join(cover_dir, key_hash + ".jpg")
        if os.path.exists(cover_path):
            cover_hashes[key] = key_hash
    return cover_hashes


def _rebuild_cover_hashes_from_disk(status: Dict) -> int:
    """Self-heal: merge the disk-derived map into status['cover_hashes'] in place.

    Thin mutating wrapper over `compute_cover_hashes_from_disk` (the single
    implementation). Returns the number of newly-added entries. Kept for the
    callers that want to repair the persisted dict; the /status read path uses
    `compute_cover_hashes_from_disk` directly so display never depends on this.
    """
    if not isinstance(status, dict):
        return 0
    computed = compute_cover_hashes_from_disk(status)
    if not computed:
        return 0
    cover_hashes = status.setdefault('cover_hashes', {})
    added = 0
    for k, h in computed.items():
        if k not in cover_hashes:
            cover_hashes[k] = h
            added += 1
    return added


def _extract_local_artwork_to_cache(status: Dict) -> int:
    """For have_locally tracks without a cover_hash, extract embedded APIC artwork."""
    if not status or not isinstance(status, dict):
        return 0
    cover_dir = _get_cover_cache_dir()
    if not os.path.isdir(cover_dir):
        try:
            os.makedirs(cover_dir, exist_ok=True)
        except OSError:
            return 0
    cover_hashes = status.setdefault('cover_hashes', {})
    try:
        from mutagen import File as _MutagenFile
    except ImportError:
        logging.warning("_extract_local_artwork: mutagen not available")
        return 0

    added = 0
    for t in (status.get('have_locally') or []):
        if not isinstance(t, dict):
            continue
        a, ti, fp = t.get('artist'), t.get('title'), t.get('filepath')
        if not a or not ti or not fp:
            continue
        base = f'{a} - {ti}'
        if base in cover_hashes or base.lower() in cover_hashes:
            continue
        if not os.path.exists(fp):
            continue
        try:
            audio = _MutagenFile(fp)
            if audio is None:
                continue
            apic_data = None
            seen_keys = set()
            try:
                seen_keys.update(audio.keys())
            except Exception:
                logging.debug("_extract_local_artwork: audio.keys() failed for %s", fp, exc_info=True)
            if hasattr(audio, 'tags') and audio.tags:
                try:
                    seen_keys.update(audio.tags.keys())
                except Exception:
                    logging.debug("_extract_local_artwork: tags.keys() failed for %s", fp, exc_info=True)
            for k in seen_keys:
                ks = str(k)
                if ks.startswith('APIC') or ks.startswith('PIC'):
                    v = None
                    try:
                        if k in audio:
                            v = audio[k]
                        elif hasattr(audio, 'tags') and audio.tags and k in audio.tags:
                            v = audio.tags[k]
                    except Exception:
                        logging.debug("_extract_local_artwork: tag lookup failed for %s", fp, exc_info=True)
                    if v and hasattr(v, 'data') and v.data:
                        apic_data = v.data
                        break
            if not apic_data:
                continue
            key_hash = hashlib.md5(base.encode('utf-8')).hexdigest()
            out_path = os.path.join(cover_dir, key_hash + '.jpg')
            if not os.path.exists(out_path):
                try:
                    with open(out_path, 'wb') as fh:
                        fh.write(apic_data)
                except OSError:
                    continue
            cover_hashes[base] = key_hash
            cover_hashes[base.lower()] = key_hash
            added += 1
        except Exception:
            logging.debug("APIC extraction failed for %s", fp, exc_info=True)
            continue
    return added


def download_cover_art(url: str) -> Optional[str]:
    """Download cover art from URL and return as base64."""
    try:
        if not url:
            return None
        response = requests.get(url, timeout=10)
        if response.status_code == 200 and response.content:
            return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        logging.warning("download_cover_art: failed to download %s — %s", url, e)
    return None
