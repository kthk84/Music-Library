#!/usr/bin/env python3
"""
Backfill Soundeo track IDs into the status cache (shazam_status_cache.json).
For each track that has a URL but no stored track_id, visits the track page via HTTP
and parses data-track-id, then saves it in status['track_ids']. Single source of
truth: only the status cache file is read and written; no separate files.

After running, star/unstar/dismiss will use HTTP when a track_id is present and
fall back to the crawler only when no ID is available.

Run from project root:
  python3 scripts/backfill_track_ids.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config_shazam import get_soundeo_cookies_path
from shazam_cache import load_status_cache, save_status_cache
from soundeo_automation import extract_track_id, get_track_id_from_page


def main():
    status = load_status_cache()
    if not status:
        print("No status cache found. Run Compare first so tracks have URLs.")
        return
    urls = status.get("urls") or {}
    if not urls:
        print("No track URLs in status cache. Nothing to backfill.")
        return
    cookies_path = get_soundeo_cookies_path()
    if not cookies_path or not os.path.exists(cookies_path):
        print("Soundeo cookies file not found. Save session in Settings first.")
        return
    track_ids = status.setdefault("track_ids", {})
    # Use a canonical set of keys (avoid processing both "Artist - Title" and "artist - title")
    seen_lower = set()
    keys_to_process = []
    for k, url in urls.items():
        if not url or not isinstance(k, str) or k.strip() == "":
            continue
        kl = k.lower()
        if kl in seen_lower:
            continue
        seen_lower.add(kl)
        if track_ids.get(k) or track_ids.get(kl):
            continue
        keys_to_process.append((k, (url or "").strip()))
    if not keys_to_process:
        print("All tracks with URLs already have track_ids. Nothing to do.")
        return
    print(f"Backfilling track_id for {len(keys_to_process)} track(s) from status cache...")
    done = 0
    failed = 0
    for key, url in keys_to_process:
        if not url.startswith(("https://soundeo.com/", "http://soundeo.com/")):
            failed += 1
            continue
        tid = extract_track_id(url)
        if not tid:
            tid = get_track_id_from_page(url, cookies_path)
        if tid:
            track_ids[key] = tid
            track_ids[key.lower()] = tid
            save_status_cache(status)
            done += 1
            print(f"  [{done}] {key[:60]}... -> {tid}")
        else:
            failed += 1
    print(f"Done. Backfilled {done} track_id(s), {failed} skipped/failed. Status cache updated.")


if __name__ == "__main__":
    main()
