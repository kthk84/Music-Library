#!/usr/bin/env python3
"""
Verify: are unmatched Shazam tracks really not on disk, or are we missing them?
For each unmatched Shazam we search ALL local filenames with:
- Lower fuzzy threshold (50%, 60%)
- Title-only match (normalized title contained in or very similar to a local title)
So we can say: truly no candidate vs we had a possible match we didn't count.
"""

import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

from rapidfuzz import fuzz, process

# Reuse same norm helpers as compare_tracks
def normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = unicodedata.normalize("NFC", s)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

def normalize_artist(artist: str) -> str:
    s = normalize(artist)
    tokens = re.split(r"\s*[&,]\s*|\s+feat\.?\s+", s, flags=re.I)
    tokens = [t.strip() for t in tokens if t.strip()]
    return " & ".join(sorted(tokens)) if tokens else s

def normalize_track_text(s: str) -> str:
    s = normalize(s)
    s = re.sub(r"\s*\([^)]*(?:mix|remix|edit|extended|radio|club|vocal|instrumental)[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\s*\[[^\]]*(?:mix|remix|edit|mixed)[^\]]*\]", "", s, flags=re.I)
    s = re.sub(r"[\s\.\-_]+$", "", s)
    s = re.sub(r"^\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s

def parse_shazam(line: str):
    line = line.strip()
    if not line or " | " not in line:
        return None
    a, t = line.split(" | ", 1)
    return (a.strip(), t.strip())

def parse_local(line: str):
    line = line.strip()
    if not line:
        return None
    line = re.sub(r"\.(aiff|mp3|flac|m4a|wav)$", "", line, flags=re.I)
    if " - " not in line:
        return None
    a, t = line.split(" - ", 1)
    return (a.strip(), t.strip())

def make_key(artist: str, title: str) -> str:
    return f"{normalize_artist(artist)}|{normalize_track_text(title)}"

def main():
    shazam_path = Path("/Users/keith/Downloads/shazam_tracks.txt")
    local_path = Path("/Users/keith/Downloads/local_filenames.txt")

    shazam = []
    with open(shazam_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = parse_shazam(line)
            if p:
                shazam.append(p)

    local = []
    with open(local_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = parse_local(line)
            if p:
                artist, title = p
                if not title:
                    continue
                # only .aiff for fair comparison with disk
                if not line.strip().lower().endswith(".aiff"):
                    continue
                local.append((artist, title, line.strip()))

    local_keys = {make_key(a, t) for a, t, _ in local}
    local_norm_full = [f"{normalize_artist(a)}|{normalize_track_text(t)}" for a, t, _ in local]
    local_titles_norm = [normalize_track_text(t) for _, t, _ in local]
    local_records = list(local)

    # Unmatched = Shazam with no exact key in local
    unmatched = []
    for (artist, title) in shazam:
        key = make_key(artist, title)
        if key not in local_keys:
            unmatched.append((artist, title))

    print(f"Unmatched Shazam tracks: {len(unmatched)}")
    print(f"Local .aiff entries: {len(local)}\n")

    # For each unmatched, best fuzzy on full "artist|title" and best on title-only
    might_be_there_60 = 0
    might_be_there_50 = 0
    title_only_candidates = 0
    no_candidate = 0
    examples_maybe = []
    examples_nowhere = []

    for (artist, title) in unmatched:
        full_norm = f"{normalize_artist(artist)}|{normalize_track_text(title)}"
        title_norm = normalize_track_text(title)
        # Best full-string match in local
        res = process.extractOne(full_norm, local_norm_full, scorer=fuzz.token_sort_ratio)
        best_full_ratio = res[1] if res else 0
        # Best title-only: does any local title (normalized) have high similarity?
        best_title_ratio = 0
        for lt in local_titles_norm:
            r = fuzz.token_sort_ratio(title_norm, lt)
            if r > best_title_ratio:
                best_title_ratio = r
        # Or is normalized title a substring of some local title?
        title_substring = any(title_norm in lt or lt in title_norm for lt in local_titles_norm if len(title_norm) > 4 and len(lt) > 4)

        if best_full_ratio >= 60 or best_title_ratio >= 85 or title_substring:
            might_be_there_60 += 1
            if len(examples_maybe) < 8:
                idx = res[2] if res and len(res) > 2 else 0
                rec = local_records[idx] if 0 <= idx < len(local_records) else None
                examples_maybe.append(((artist, title), best_full_ratio, rec))
        if best_full_ratio >= 50:
            might_be_there_50 += 1
        if best_full_ratio < 50 and best_title_ratio < 75 and not title_substring:
            no_candidate += 1
            if len(examples_nowhere) < 8:
                examples_nowhere.append((artist, title))
        if best_title_ratio >= 80 or title_substring:
            title_only_candidates += 1

    print("--- Verification (looser matching) ---")
    print(f"Unmatched Shazam with some local candidate (full≥60% or title≥85% or title substring): {might_be_there_60}")
    print(f"Unmatched Shazam with full-string fuzzy ≥50%: {might_be_there_50}")
    print(f"Unmatched Shazam with title-only match (≥80% or substring): {title_only_candidates}")
    print(f"Unmatched Shazam with NO good candidate (full<50%, title<75%, no substring): {no_candidate}")
    print()
    print("Conclusion: if 'no good candidate' is most of them, those tracks are really not in the local list.")
    print()
    if examples_maybe:
        print("Examples of unmatched Shazam that MIGHT have a local file (we could add looser rules):")
        for ((a, t), ratio, rec) in examples_maybe:
            print(f"  Shazam: {a} | {t}")
            if rec:
                print(f"  Local:  {rec[2][:80]}...  (full ratio {ratio:.0f}%)")
            print()
    if examples_nowhere:
        print("Examples of unmatched Shazam with NO plausible local match:")
        for (a, t) in examples_nowhere:
            print(f"  {a} | {t}")

if __name__ == "__main__":
    main()
