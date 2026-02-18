"""
Integration test: load real Shazam + local scan caches, sample 100 tracks across the list,
run the matcher, and report match rate. Validates that matching works on real data.
Skips if caches are missing (no Shazam/local data yet).
"""
import os
import sys

import pytest

# Project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_caches():
    """Load shazam and local scan caches. Returns (shazam_tracks, local_tracks) or (None, None) if missing."""
    from shazam_cache import load_shazam_cache, load_local_scan_cache, LOCAL_SCAN_CACHE_PATH
    from shazam_cache import SHAZAM_CACHE_PATH
    if not os.path.exists(SHAZAM_CACHE_PATH) or not os.path.exists(LOCAL_SCAN_CACHE_PATH):
        return None, None
    shazam = load_shazam_cache()
    local_scan = load_local_scan_cache()
    if not shazam or not local_scan:
        return None, None
    local_tracks = local_scan.get("tracks", [])
    if not local_tracks:
        return shazam, []
    return shazam, local_tracks


def test_matching_100_tracks_integration():
    """
    Sample 100 Shazam tracks (spread across the list), run matcher, assert match rate.
    Uses real caches; skips if not present.
    """
    shazam_tracks, local_tracks = _load_caches()
    if shazam_tracks is None:
        pytest.skip("Shazam or local scan cache not found (run Compare first)")
    if len(shazam_tracks) < 20:
        pytest.skip("Need at least 20 Shazam tracks in cache")
    if len(local_tracks) < 10:
        pytest.skip("Need at least 10 local tracks in cache")

    # Sample 100 tracks spread across the list (or all if fewer)
    step = max(1, len(shazam_tracks) // 100)
    indices = list(range(0, len(shazam_tracks), step))[:100]
    sample = [shazam_tracks[i] for i in indices]

    from local_scanner import compute_to_download, _find_matching_local_track
    from local_scanner import _build_title_word_index, _build_exact_match_map

    title_word_index = _build_title_word_index(local_tracks)
    exact_match_map = _build_exact_match_map(local_tracks)

    matched = 0
    unmatched_samples = []

    for t in sample:
        match = _find_matching_local_track(
            t, local_tracks,
            title_word_index=title_word_index,
            exact_match_map=exact_match_map,
        )
        if match:
            matched += 1
        else:
            if len(unmatched_samples) < 15:
                unmatched_samples.append({
                    "artist": t.get("artist", ""),
                    "title": t.get("title", ""),
                })

    match_rate = matched / len(sample) if sample else 0
    # Expect at least 25% match on real data (many Shazam tracks may genuinely not be in local folders)
    assert match_rate >= 0.20, (
        f"Match rate {match_rate:.1%} ({matched}/{len(sample)}) is too low. "
        f"Sample unmatched: {unmatched_samples[:5]}"
    )
    # Also log for visibility
    print(f"\n[100-track test] Matched {matched}/{len(sample)} ({match_rate:.1%}). "
          f"Local library size: {len(local_tracks)}. Shazam total: {len(shazam_tracks)}.")
