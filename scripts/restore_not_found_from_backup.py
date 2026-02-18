#!/usr/bin/env python3
"""
Restore the not_found paper trail from a backup so dots show correctly:
- Orange = searched on Soundeo but not found (keys in not_found)
- Grey = never searched (no link and not in not_found)

Uses shazam_status_cache.json.bak if present; otherwise exits.
Also removes from not_found any track that now has a URL (so orange only for no-link tracks).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shazam_cache import load_status_cache, save_status_cache, STATUS_CACHE_PATH

BACKUP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shazam_status_cache.json.bak")


def main():
    if not os.path.exists(BACKUP_PATH):
        print("No backup found at shazam_status_cache.json.bak")
        return 1
    status = load_status_cache()
    if not status:
        print("No current status cache. Run Fetch + Compare in the app first.")
        return 1
    with open(BACKUP_PATH, "r", encoding="utf-8") as f:
        import json
        backup = json.load(f)
    old_not_found = dict(backup.get("not_found") or {})
    urls = status.get("urls") or {}
    # Only keep in not_found keys that still have no URL
    not_found = {}
    for k, v in old_not_found.items():
        if v and not (urls.get(k) or urls.get(k.lower() if isinstance(k, str) else None)):
            not_found[k] = True
    status["not_found"] = not_found
    save_status_cache(status)
    print("Restored not_found from backup ({} entries; {} kept after dropping those that now have a URL). Dots: orange = searched not found, grey = never searched.".format(len(old_not_found), len(not_found)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
