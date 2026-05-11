#!/usr/bin/env python3
"""
Rebuild urls and not_found in shazam_status_cache.json from the search outcome log.
Use this to recover correct dot state (grey vs orange vs link) if the status cache
was corrupted or mass-changed. The log records every search result (found/not_found)
per track; replaying it (newest per key wins) restores urls and not_found.

Run from project root:
  python3 scripts/rebuild_status_from_search_log.py
"""
import os
import sys

# Project root = parent of scripts/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from shazam_cache import load_status_cache, save_status_cache, rebuild_status_from_search_log

def main():
    existing = load_status_cache()
    log = (existing or {}).get("search_outcomes") or []
    if not log:
        print("No search_outcomes in status cache. Nothing to rebuild.")
        return
    rebuilt = rebuild_status_from_search_log(existing)
    save_status_cache(rebuilt)
    urls = rebuilt.get("urls") or {}
    nf = rebuilt.get("not_found") or {}
    print(f"Rebuilt from {len(log)} search_outcomes in status cache. urls: {len(urls)} entries, not_found: {len(nf)} entries. Saved.")

if __name__ == "__main__":
    main()
