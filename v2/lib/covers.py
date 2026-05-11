"""Cover art helpers — extracted from app.py in session 9 (2026-04-29)."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Dict, Optional

import requests

from app_paths import get_project_root_for_data
from .keys import _strip_all_parens, _deep_norm_key


def _get_cover_cache_dir() -> str:
    """Return (and create) the directory for locally cached cover art images."""
    base = get_project_root_for_data(__file__)
    path = os.path.join(base, "cover_cache")
    os.makedirs(path, exist_ok=True)
    return path


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
    """Store cover hash under multiple key variants so UI lookups always hit."""
    if not status or not key or not cover_hash:
        return
    status.setdefault('cover_hashes', {})
    k = (key or '').strip()
    if not k:
        return
    variants = set()
    variants.add(k)
    variants.add(k.lower())
    try:
        norm = _strip_all_parens(k).strip()
        if norm:
            variants.add(norm)
            variants.add(norm.lower())
    except Exception:
        logging.debug("_set_cover_hash_variants: strip-parens variant failed", exc_info=True)
    try:
        deep = _deep_norm_key(k)
        if deep:
            variants.add(deep)
            variants.add(deep.lower())
    except Exception:
        logging.debug("_set_cover_hash_variants: deep-norm variant failed", exc_info=True)
    for v in variants:
        if v and v not in status['cover_hashes']:
            status['cover_hashes'][v] = cover_hash


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
    """Self-heal status['cover_hashes'] by md5-mapping known keys to existing cover_cache files."""
    if not status or not isinstance(status, dict):
        return 0
    cover_dir = _get_cover_cache_dir()
    if not os.path.isdir(cover_dir):
        return 0
    try:
        existing_hashes = set()
        for fn in os.listdir(cover_dir):
            low = fn.lower()
            if low.endswith('.jpg') or low.endswith('.jpeg') or low.endswith('.png'):
                existing_hashes.add(fn.rsplit('.', 1)[0].lower())
    except OSError as e:
        logging.warning("_rebuild_cover_hashes_from_disk: listdir failed: %s", e)
        return 0
    if not existing_hashes:
        return 0
    cover_hashes = status.setdefault('cover_hashes', {})
    urls = status.get('urls') or {}
    soundeo_titles = status.get('soundeo_titles') or {}
    starred = status.get('starred') or {}
    added = 0
    for key in list(urls.keys()):
        if not isinstance(key, str) or key in cover_hashes:
            continue
        h = hashlib.md5(key.encode('utf-8')).hexdigest()
        if h in existing_hashes:
            cover_hashes[key] = h
            added += 1
    for key in list(starred.keys()):
        if not isinstance(key, str) or key in cover_hashes:
            continue
        h = hashlib.md5(key.encode('utf-8')).hexdigest()
        if h in existing_hashes:
            cover_hashes[key] = h
            added += 1
    for src in ('have_locally', 'to_download', 'skipped_tracks'):
        for t in (status.get(src) or []):
            if not isinstance(t, dict):
                continue
            a, ti = t.get('artist'), t.get('title')
            if not a or not ti:
                continue
            base = f'{a} - {ti}'
            if base in cover_hashes:
                continue
            sd = soundeo_titles.get(base) or soundeo_titles.get(base.lower())
            candidates = [base, base.lower()]
            if sd and isinstance(sd, str):
                candidates += [sd, sd.lower(), f'{a} - {sd}', f'{a} - {sd}'.lower()]
            for c in candidates:
                h = hashlib.md5(c.encode('utf-8')).hexdigest()
                if h in existing_hashes:
                    cover_hashes[base] = h
                    cover_hashes[base.lower()] = h
                    added += 1
                    break
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
