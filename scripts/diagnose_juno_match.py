#!/usr/bin/env python3
"""Diagnose why Joi N'Juno - Musik stays purple after rescan."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shazam_cache import load_shazam_cache, load_local_scan_cache
from local_scanner import normalize, _build_exact_match_map, _find_matching_local_track
from app import _track_key_norm

shazam = load_shazam_cache()
local = load_local_scan_cache()
local_tracks = (local or {}).get("tracks") or []

# Find Joi N'Juno / Musik in Shazam
s_match = [t for t in (shazam or []) if "joi" in (t.get("artist") or "").lower() and "musik" in (t.get("title") or "").lower()]
print("Shazam (Joi + Musik):", [(t.get("artist"), t.get("title")) for t in s_match[:5]])

# Find in local scan
l_match = [t for t in local_tracks if "joi" in (t.get("artist") or "").lower() or "musik" in (t.get("title") or "").lower() or "juno" in (t.get("artist") or "").lower()]
print("Local scan (Joi/Musik/Juno):", [(t.get("artist"), t.get("title"), (t.get("filepath") or "")[:70]) for t in l_match[:10]])

# Exact match map - what key would we need?
exact = _build_exact_match_map(local_tracks) if local_tracks else {}
for k, v in list(exact.items())[:20]:
    if "joi" in str(k).lower() or "musik" in str(k).lower() or "juno" in str(k).lower():
        print("Exact key:", repr(k), "->", v.get("filepath", "")[:60])

# If we have both, run _find_matching_local_track
if s_match and local_tracks:
    from local_scanner import compute_to_download
    _, ti, em, lc = compute_to_download(shazam, local_tracks)
    for s in s_match[:3]:
        m, sc = _find_matching_local_track(s, local_tracks, exact_match_map=em, local_canon=lc, title_word_index=ti)
        print("Match for", s.get("artist"), "-", s.get("title"), ":", "FOUND" if m else "NOT FOUND", "score", sc)
        if m:
            print("  ->", m.get("filepath", "")[:80])

# Test normalize variants
print("\nNormalize variants:")
for raw in ["Joi N'Juno", "Joi N Juno", "Joi N´Juno"]:
    n = normalize(raw)
    print(f"  {repr(raw)} -> {repr(n)}")
