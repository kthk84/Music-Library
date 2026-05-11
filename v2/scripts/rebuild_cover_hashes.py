#!/usr/bin/env python3
"""
Rebuild status['cover_hashes'] in shazam_status_cache.json by md5-mapping
URL keys to existing cover_cache/<md5>.jpg files.

Use when the UI shows missing thumbnails but cover_cache/ has files on disk:
the cover_hashes dict was wiped by a stale rebuild path (see commits e5074f5,
311b429) and the frontend can't look up images. This script reconstructs the
mapping deterministically from disk — never re-downloads, never overwrites.

Run from project root:
    python3 scripts/rebuild_cover_hashes.py

The Flask app self-heals on every /status fetch (see app.py
_rebuild_cover_hashes_from_disk), so this script is mainly for: (a) recovering
the persisted file immediately without restarting, (b) verifying the fix.
"""
import os
import sys

# Project root = parent of scripts/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shazam_cache import load_status_cache, save_status_cache
from app import _rebuild_cover_hashes_from_disk


def main() -> int:
    status = load_status_cache()
    if not status:
        print("No status cache found. Nothing to rebuild.")
        return 1
    before = len(status.get('cover_hashes') or {})
    urls = len(status.get('urls') or {})
    added = _rebuild_cover_hashes_from_disk(status)
    after = len(status.get('cover_hashes') or {})
    save_status_cache(status)
    print(f"cover_hashes: {before} -> {after} entries (added {added}, urls={urls})")
    if added == 0 and before == 0:
        print("WARN: rebuild added 0 entries. Check that cover_cache/ contains files "
              "named <md5(key)>.jpg matching keys in status['urls'].")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
