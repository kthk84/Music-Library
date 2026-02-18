#!/usr/bin/env python3
"""
One-time script: clear the not_found paper trail so all no-link tracks show grey.

After running this:
- Grey dot = no Soundeo link, never searched (or paper trail cleared).
- When you run Search (all or per-row) again, any track searched but not found
  is recorded and will show orange (searched, not found).

The app remembers this in shazam_status_cache.json (not_found). Run this script
once when you want to "start fresh" so only future searches set orange.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shazam_cache import load_status_cache, save_status_cache


def main():
    status = load_status_cache()
    if not status:
        print("No status cache found. Run Fetch + Compare in the app first.")
        return 1
    prev = len(status.get("not_found") or {})
    status["not_found"] = {}
    save_status_cache(status)
    print("Done. Cleared not_found ({} entries). All no-link tracks will show grey until you run Search again.".format(prev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
