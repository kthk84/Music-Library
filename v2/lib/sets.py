"""Tracklist sets: paste a URL → scrape the page → persist tracks as a named set.

Supported sources:
- trackid.net audiostream pages — the site is an SPA; tracks come from its
  public JSON API (https://trackid.net/api/public/audiostreams/<slug>), merged
  across all detection processes, ordered by start time.
- 1001tracklists.com tracklist pages — Cloudflare-protected, so a plain GET is
  usually blocked; callers can supply a `fetch_html` callable (the app passes a
  Selenium fetch reusing the Soundeo browser profile). Parsed via the page's
  JSON-LD (MusicPlaylist/ItemList) with a DOM-regex fallback.
- Anything else — best effort: JSON-LD first, then "NN. Artist - Title" lines.

Sets persist to sets.json (atomic write) in the shared data dir, so they
survive refresh and restart like every other cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from html import unescape
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests

from app_paths import get_project_root_for_data

SETS_PATH = os.path.join(get_project_root_for_data(__file__), "sets.json")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------- storage ---

def load_sets() -> List[Dict]:
    try:
        with open(SETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        sets = data.get("sets") or []
        return sets if isinstance(sets, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_sets(sets: List[Dict]) -> None:
    from shazam_cache import _save_json_atomic
    _save_json_atomic(SETS_PATH, {"sets": sets, "updated_at": int(time.time())})


def set_id_for_url(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:12]


# ----------------------------------------------------------------- helpers ---

def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", unescape(str(s or ""))).strip()


def _mk_track(artist: str, title: str, start_time: str = "") -> Dict:
    return {"artist": _clean(artist), "title": _clean(title), "start_time": _clean(start_time)}


def _http_get(url: str, timeout: int = 20) -> requests.Response:
    return requests.get(url, timeout=timeout, headers={"User-Agent": _UA, "Accept-Language": "en"})


# ------------------------------------------------------------ trackid.net ---

def _scrape_trackid(url: str) -> Dict:
    """trackid.net audiostream → tracks from the public API, all detection
    processes merged, deduped by (artist,title) keeping the earliest start."""
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1]
    if not slug:
        raise ValueError("Could not extract the audiostream slug from that trackid.net URL.")
    api = f"https://trackid.net/api/public/audiostreams/{slug}"
    resp = _http_get(api)
    if resp.status_code != 200:
        raise ValueError(f"trackid.net API returned HTTP {resp.status_code} for {slug}.")
    result = (resp.json() or {}).get("result") or {}
    title = _clean(result.get("title")) or slug
    merged: Dict[tuple, Dict] = {}
    for proc in result.get("detectionProcesses") or []:
        for t in proc.get("detectionProcessMusicTracks") or []:
            artist, ttl = _clean(t.get("artist")), _clean(t.get("title"))
            if not artist and not ttl:
                continue
            k = (artist.lower(), ttl.lower())
            start = _clean(t.get("startTime"))
            if k not in merged or (start and start < merged[k]["start_time"]):
                merged[k] = _mk_track(artist, ttl, start)
    tracks = sorted(merged.values(), key=lambda x: x["start_time"] or "99:99:99")
    if not tracks:
        raise ValueError("trackid.net returned no detected tracks for that stream (it may still be processing).")
    return {"title": title, "tracks": tracks, "source": "trackid.net",
            "stream_url": _clean(result.get("url"))}


# ------------------------------------------------------------------ JSON-LD ---

def _tracks_from_jsonld(html: str) -> List[Dict]:
    """Parse schema.org MusicPlaylist / ItemList JSON-LD blocks into tracks."""
    tracks: List[Dict] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            track_block = obj.get("track") or obj.get("tracks") or (
                obj if obj.get("@type") in ("ItemList",) else None
            )
            items = []
            if isinstance(track_block, dict):
                items = track_block.get("itemListElement") or []
            elif isinstance(track_block, list):
                items = track_block
            for el in items:
                if not isinstance(el, dict):
                    continue
                item = el.get("item") if isinstance(el.get("item"), dict) else el
                name = _clean(item.get("name"))
                by = item.get("byArtist")
                artist = ""
                if isinstance(by, dict):
                    artist = _clean(by.get("name"))
                elif isinstance(by, list):
                    artist = _clean(", ".join(_clean(a.get("name")) for a in by if isinstance(a, dict)))
                elif isinstance(by, str):
                    artist = _clean(by)
                if not artist and " - " in name:
                    artist, name = name.split(" - ", 1)
                if name or artist:
                    tracks.append(_mk_track(artist, name))
    return tracks


def _jsonld_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    return _clean(m.group(1)) if m else ""


# -------------------------------------------------------- 1001tracklists ---

_CF_MARKERS = ("cf-browser-verification", "challenge-platform", "Just a moment", "_cf_chl")


def _looks_blocked(resp_text: str, status: int) -> bool:
    if status in (403, 503, 429, 206):
        return True
    low = resp_text[:4000].lower()
    return any(mk.lower() in low for mk in _CF_MARKERS)


def _tracks_from_1001_dom(html: str) -> List[Dict]:
    """DOM parse for 1001tracklists (verified against a real headed-browser
    page, 2026-06): each track row is a schema.org MusicRecording itemscope
    carrying `<meta itemprop="name" content="Artist - Title">` and usually
    `<meta itemprop="byArtist" content="Artist">`. Those metas are the precise
    source — parse them FIRST, using byArtist (when adjacent) to split
    artist/title even if the artist name itself contains " - ". Fallback:
    visible `.trackValue` span text."""
    out: List[Dict] = []
    # Scope to MusicRecording itemscopes so page-level itemprop="name" metas
    # (page title, site name) can't leak in as tracks. Each block's window runs
    # to the next MusicRecording (or a bounded slice for the last one).
    rec_starts = [m.start() for m in re.finditer(
        r'itemtype=["\']https?://schema\.org/MusicRecording["\']', html)]
    for idx, start in enumerate(rec_starts):
        end = rec_starts[idx + 1] if idx + 1 < len(rec_starts) else min(len(html), start + 6000)
        block = html[start:end]
        name_m = re.search(r'<meta[^>]+itemprop=["\']name["\'][^>]+content="([^"]*)"', block)
        if not name_m:
            continue
        content = _clean(name_m.group(1))
        if not content:
            continue
        by_m = re.search(r'<meta[^>]+itemprop=["\']byArtist["\'][^>]+content="([^"]*)"', block)
        by_artist = _clean(by_m.group(1)) if by_m else ""
        if by_artist and content.lower().startswith(by_artist.lower() + " - "):
            out.append(_mk_track(content[:len(by_artist)], content[len(by_artist) + 3:]))
        elif " - " in content:
            artist, title = content.split(" - ", 1)
            out.append(_mk_track(artist, title))
        else:
            out.append(_mk_track(by_artist, content))
    if out:
        return out
    # Fallback: visible trackValue spans (strip markup, join fragments).
    for m in re.finditer(r'<span[^>]*class="[^"]*trackValue[^"]*"[^>]*>(.*?)</span>\s*(?=<|$)', html, re.DOTALL):
        text = _clean(re.sub(r"<[^>]+>", "", m.group(1)))
        if not text:
            continue
        if " - " in text:
            artist, title = text.split(" - ", 1)
            out.append(_mk_track(artist, title))
        else:
            out.append(_mk_track("", text))
    return out


def _extract_stream_url(html: str) -> str:
    """Best-effort: find the playable source stream (the actual mix audio) in a
    tracklist page. Preference order: SoundCloud api-track reference (loads
    directly in the SC widget), YouTube video, SoundCloud permalink, Mixcloud.
    Returns '' when nothing is found."""
    if not html:
        return ""
    m = re.search(r"(?:api\.)?soundcloud\.com/tracks/(\d+)", html)
    if m:
        return f"https://api.soundcloud.com/tracks/{m.group(1)}"
    m = re.search(r"(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([a-zA-Z0-9_-]{6,15})", html)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    # SoundCloud permalink (skip 1001tracklists' own profile + player assets)
    for m in re.finditer(r"https?://soundcloud\.com/([a-zA-Z0-9_-]+)(/[a-zA-Z0-9_-]+)?", html):
        user, slug = m.group(1), m.group(2) or ""
        if user in ("1001tracklists", "player", "pages", "you", "search", "tags"):
            continue
        if slug and slug not in ("/tracks", "/sets", "/likes", "/followers"):
            return f"https://soundcloud.com/{user}{slug}"
    m = re.search(r"https?://(?:www\.)?mixcloud\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/?", html)
    if m:
        return m.group(0)
    return ""


def has_tracklist_markers(html: str) -> bool:
    """True when the HTML looks like a real tracklist page (not an interstitial)."""
    low = (html or "").lower()
    return (
        "application/ld+json" in low
        or low.count("trackvalue") > 3
        or 'itemprop="tracks"' in low
    )


def _scrape_1001(url: str, fetch_html: Optional[Callable[[str], str]] = None) -> Dict:
    html = ""
    try:
        resp = _http_get(url)
        if not _looks_blocked(resp.text, resp.status_code):
            html = resp.text
    except requests.RequestException as e:
        logging.info("sets: plain GET failed for 1001tracklists (%s)", e)
    if not html:
        if not fetch_html:
            raise ValueError(
                "1001tracklists blocked the direct request (Cloudflare) and no browser fetch is available."
            )
        html = fetch_html(url) or ""
        if not html:
            raise ValueError("Could not load the 1001tracklists page via the browser.")
    tracks = _tracks_from_jsonld(html) or _tracks_from_1001_dom(html)
    if not tracks:
        raise ValueError("No tracks found on that 1001tracklists page (layout may have changed).")
    title = _jsonld_title(html) or url
    title = re.sub(r"\s*\|\s*1001Tracklists.*$", "", title, flags=re.IGNORECASE)
    return {"title": title, "tracks": tracks, "source": "1001tracklists",
            "stream_url": _extract_stream_url(html)}


# ----------------------------------------------------------------- generic ---

_LINE_RE = re.compile(r"^\s*(?:\d{1,3}[.)]\s*)?(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?(.{2,120}?)\s+-\s+(.{2,160}?)\s*$")


def _scrape_generic(url: str, fetch_html: Optional[Callable[[str], str]] = None) -> Dict:
    html = ""
    try:
        resp = _http_get(url)
        if not _looks_blocked(resp.text, resp.status_code):
            html = resp.text
    except requests.RequestException:
        html = ""
    if not html and fetch_html:
        html = fetch_html(url) or ""
    if not html:
        raise ValueError("Could not load that page.")
    tracks = _tracks_from_jsonld(html)
    if not tracks:
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "\n", text)
        seen = set()
        for line in text.splitlines():
            m = _LINE_RE.match(unescape(line))
            if not m:
                continue
            artist, title = _clean(m.group(1)), _clean(m.group(2))
            if len(artist) < 2 or len(title) < 2:
                continue
            k = (artist.lower(), title.lower())
            if k in seen:
                continue
            seen.add(k)
            tracks.append(_mk_track(artist, title))
        tracks = tracks[:200]
    if not tracks:
        raise ValueError("No tracklist found on that page.")
    return {"title": _jsonld_title(html) or url, "tracks": tracks,
            "source": urlparse(url).netloc or "web", "stream_url": _extract_stream_url(html)}


# -------------------------------------------------------------------- main ---

def scrape_set_from_url(url: str, fetch_html: Optional[Callable[[str], str]] = None) -> Dict:
    """Scrape `url` into a set dict: {id, url, source, title, created_at, tracks}.

    Raises ValueError with a user-readable message when nothing can be scraped.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("That doesn't look like a URL.")
    host = (urlparse(url).netloc or "").lower()
    if "trackid.net" in host:
        scraped = _scrape_trackid(url)
    elif "1001tracklists.com" in host:
        scraped = _scrape_1001(url, fetch_html)
    else:
        scraped = _scrape_generic(url, fetch_html)
    return {
        "id": set_id_for_url(url),
        "url": url,
        "source": scraped["source"],
        "title": scraped["title"],
        "created_at": int(time.time()),
        "tracks": scraped["tracks"],
        "track_count": len(scraped["tracks"]),
        "stream_url": scraped.get("stream_url") or "",
    }


def add_or_replace_set(new_set: Dict) -> List[Dict]:
    """Insert (or refresh, same URL → same id) a set; newest first. Persists."""
    sets = [s for s in load_sets() if s.get("id") != new_set.get("id")]
    sets.insert(0, new_set)
    save_sets(sets)
    return sets


def delete_set(set_id: str) -> List[Dict]:
    sets = [s for s in load_sets() if s.get("id") != set_id]
    save_sets(sets)
    return sets
