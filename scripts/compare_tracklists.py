#!/usr/bin/env python3
"""
Compare local vs Shazam tracklists and analyze why the tool reports "to download".

1. Build LOCAL tracklist from configured folders (using local_scan_cache).
2. Build SHAZAM tracklist from shazam_cache.
3. Run a simple canonical match: count how many Shazam tracks could match local (canon title + canon artist match or contain).
4. Run the tool's compute_to_download to get TO_DOWNLOAD.
5. Compare numbers and, for each to_download track, analyze WHY it wasn't matched (no candidate? had candidate but artist/title failed?).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_shazam import get_destination_folders
from shazam_cache import (
    load_shazam_cache,
    load_local_scan_cache,
    local_scan_cache_valid,
    load_skip_list,
)
from local_scanner import (
    compute_to_download,
    _canon,
    _canon_match,
    normalize,
    _find_matching_local_track,
    _build_title_word_index,
    _build_exact_match_map,
)
from app import similarity_score


def main():
    folder_paths = get_destination_folders()
    if not folder_paths:
        print("No destination folders configured or found. Add folders in Settings.")
        return

    local_scan = load_local_scan_cache()
    if not local_scan_cache_valid(local_scan, folder_paths):
        print("Local scan cache invalid or missing. Run Compare in the app first to build it.")
        return

    local_tracks = local_scan.get("tracks", [])
    shazam_tracks = load_shazam_cache()
    skipped = load_skip_list()

    if not shazam_tracks:
        print("No Shazam tracks. Fetch Shazam first.")
        return

    # ---- 1) Canonical match: how many Shazam could match local by "name vs name" only ----
    local_canon = [(_canon(lt["title"]), _canon(lt["artist"])) for lt in local_tracks]
    canonical_matched = 0
    for i, s in enumerate(shazam_tracks):
        if (s["artist"].strip().lower(), s["title"].strip().lower()) in skipped:
            continue
        st, sa = _canon(s["title"]), _canon(s["artist"])
        for (lt, la) in local_canon:
            if (st == lt or st in lt or lt in st) and (sa == la or sa in la or la in sa):
                canonical_matched += 1
                break

    # ---- 2) Tool's to_download ----
    to_download_raw, title_word_index, exact_match_map, _ = compute_to_download(
        shazam_tracks, local_tracks
    )
    to_download = [
        t
        for t in to_download_raw
        if (t["artist"].strip().lower(), t["title"].strip().lower()) not in skipped
    ]
    tool_matched = len(shazam_tracks) - len(to_download)  # after skip filter we use same count
    tool_to_download = len(to_download)

    # ---- 3) Report numbers ----
    print("=== TRACKLISTS ===")
    print(f"Local (from configured folders): {len(local_tracks)} tracks")
    print(f"Shazam:                          {len(shazam_tracks)} tracks")
    print()
    print("=== CANONICAL MATCH (name vs name, letters only, one can contain other) ===")
    print(f"Shazam tracks that have a canonical match in local: {canonical_matched}")
    print(f"Shazam not matchable by canonical:                 {len(shazam_tracks) - canonical_matched}")
    print()
    print("=== TOOL (app matcher) ===")
    print(f"Tool 'Have locally' (matched): {tool_matched}")
    print(f"Tool 'To download':            {tool_to_download}")
    print()
    gap = canonical_matched - tool_matched
    print("=== GAP (learn from this) ===")
    if gap > 0:
        print(f"Canonical says {canonical_matched} could match; tool only matched {tool_matched}.")
        print(f"So {gap} tracks are matchable by name but the tool did not match them.")
    else:
        print("Tool matches at least as many as canonical (or canonical is stricter).")
    print()

    # ---- 4) For each to_download, why not matched? ----
    to_dl_set = {(t["artist"], t["title"]) for t in to_download}
    reasons = {"no_title_candidate": 0, "canon_both_match_tool_fail": 0, "title_ok_artist_canon_fail": 0}
    sample = []
    for s in shazam_tracks:
        if (s["artist"], s["title"]) not in to_dl_set:
            continue
        # This Shazam track is in to_download. Find why.
        n_title = _canon(s["title"])
        n_artist = _canon(s["artist"])
        locals_with_same_title = [
            (j, lt)
            for j, lt in enumerate(local_tracks)
            if _canon_match(s["title"], lt["title"])
        ]
        if not locals_with_same_title:
            reasons["no_title_candidate"] += 1
            if len(sample) < 30:
                sample.append(
                    {
                        "reason": "no_title_candidate",
                        "artist": s["artist"][:50],
                        "title": s["title"][:50],
                        "detail": "No local track has same canonical title",
                    }
                )
            continue
        # Has at least one local with same canonical title. Check artist.
        artist_ok = [j for j, lt in locals_with_same_title if _canon_match(s["artist"], lt["artist"])]
        if artist_ok:
            reasons["canon_both_match_tool_fail"] += 1
            # Canonically both match - so tool should have matched. Find best local and sim scores.
            j, lt = locals_with_same_title[0], locals_with_same_title[0][1]
            art_sim = similarity_score(normalize(s["artist"]), normalize(lt["artist"]))
            tit_sim = similarity_score(normalize(s["title"]), normalize(lt["title"]))
            if len(sample) < 30:
                sample.append(
                    {
                        "reason": "canon_ok_tool_fail",
                        "artist": s["artist"][:50],
                        "title": s["title"][:50],
                        "local_artist": lt["artist"][:50],
                        "local_title": lt["title"][:50],
                        "artist_sim": round(art_sim, 3),
                        "title_sim": round(tit_sim, 3),
                        "detail": "Canonical match exists but tool rejected (sim or other rule)",
                    }
                )
        else:
            reasons["title_ok_artist_canon_fail"] += 1
            j, lt = locals_with_same_title[0], locals_with_same_title[0][1]
            art_sim = similarity_score(normalize(s["artist"]), normalize(lt["artist"]))
            tit_sim = similarity_score(normalize(s["title"]), normalize(lt["title"]))
            if len(sample) < 30:
                sample.append(
                    {
                        "reason": "title_ok_artist_canon_fail",
                        "artist": s["artist"][:50],
                        "title": s["title"][:50],
                        "local_artist": lt["artist"][:50],
                        "local_title": lt["title"][:50],
                        "artist_sim": round(art_sim, 3),
                        "title_sim": round(tit_sim, 3),
                        "detail": "Same canonical title but artist canonical doesn't match",
                    }
                )

    print("=== WHY TO_DOWNLOAD NOT MATCHED (reasons) ===")
    for k, v in reasons.items():
        print(f"  {k}: {v}")
    print()
    print("=== SAMPLE (first 30) TO_DOWNLOAD WITH REASON ===")
    for i, row in enumerate(sample[:30]):
        print(f"  [{i+1}] {row.get('reason')} | Shazam: {row.get('artist')} | {row.get('title')}")
        print(f"       {row.get('detail')}")
        if row.get("local_artist") is not None:
            print(f"       Local: {row.get('local_artist')} | {row.get('local_title')} | sim A={row.get('artist_sim')} T={row.get('title_sim')}")
        print()


if __name__ == "__main__":
    main()
