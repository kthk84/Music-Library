#!/usr/bin/env python3
"""
Restore status/context per track from a numbered backup (e.g. shazam_status_cache 8.json).
Use when the main shazam_status_cache.json lost urls, search_outcomes, track_ids, starred, etc.
Merges: keeps current to_download/have_locally/folder_stats/skipped_tracks, restores from
backup: search_outcomes, urls, not_found, track_ids, starred, soundeo_titles, dismissed_manual_check.
"""
import os
import sys
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shazam_cache import load_status_cache, save_status_cache, STATUS_CACHE_PATH

BACKUP_NAME = "shazam_status_cache 8.json"
CONTEXT_KEYS = (
    "search_outcomes", "urls", "not_found", "track_ids", "starred",
    "soundeo_titles", "soundeo_match_scores", "dismissed_manual_check",
)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backup_path = os.path.join(root, BACKUP_NAME)
    if not os.path.exists(backup_path):
        print(f"No backup found at {BACKUP_NAME}")
        return 1

    current = load_status_cache() or {}
    with open(backup_path, "r", encoding="utf-8") as f:
        backup = json.load(f)

    # Backup current before overwriting
    if os.path.exists(STATUS_CACHE_PATH):
        bak_path = STATUS_CACHE_PATH + ".pre_restore.bak"
        shutil.copy2(STATUS_CACHE_PATH, bak_path)
        print(f"Backed up current status to {os.path.basename(bak_path)}")

    # Merge: keep compare state from current, restore context from backup
    out = dict(current)
    for key in CONTEXT_KEYS:
        if key in backup and backup[key]:
            out[key] = backup[key] if not isinstance(backup[key], dict) else dict(backup[key])
            if isinstance(backup[key], list):
                out[key] = list(backup[key])
    # Ensure we didn't leave empty dicts where backup had data
    for key in CONTEXT_KEYS:
        if key not in out and key in backup:
            out[key] = backup[key]

    save_status_cache(out)
    so = out.get("search_outcomes") or []
    urls = out.get("urls") or {}
    nf = out.get("not_found") or {}
    print(f"Restored status from {BACKUP_NAME}: search_outcomes={len(so)}, urls={len(urls)}, not_found={len(nf)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
