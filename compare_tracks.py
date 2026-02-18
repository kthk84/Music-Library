#!/usr/bin/env python3
"""
Compare Shazam tracks vs local filenames using title+artist and fuzzy matching.

HOW TO MATCH THESE:
1. Normalize format: Shazam uses "Artist | Title", local uses "Artist - Title (Mix).aiff".
2. Key = artist + title: Build a comparable key from both (normalized).
3. Artist normalization: Lowercase, strip accents (NFD), collapse spaces. Sort "A & B" / "B, A"
   so artist order doesn't matter (e.g. "Cevin Fisher, DJ Chus" matches "DJ Chus & Cevin Fisher").
4. Title normalization: Strip (Original Mix), (Extended Mix), [Mixed], (Remix) etc. so same
   track with different suffix still matches.
5. Exact match: Compare normalized keys. Gets ~41% of Shazam (1247 with artist-order norm).
6. Fuzzy match: For the rest, use token_sort_ratio on "artist|title" (rapidfuzz). Accept
   70%+ as matchable, 80%+ for higher confidence. One local file can only match one Shazam.
7. To approach 80% matchable you need: (a) the two lists to actually overlap that much, or
   (b) title-only matching for very unique titles, or (c) manual review of 70–79% matches.
   Current run: ~51% matchable (70%+), ~47% at 80%+; many Shazam/local rows are different tracks.
"""

import re
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

from rapidfuzz import fuzz, process


def normalize(s: str) -> str:
    """Normalize for comparison: lowercase, collapse spaces, remove accents."""
    if not s:
        return ""
    # NFD -> strip combining chars -> NFC
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = unicodedata.normalize("NFC", s)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_artist(artist: str) -> str:
    """Normalize artist so 'A & B' and 'B & A' match. Sort by token."""
    s = normalize(artist)
    # Split on & , and feat. and sort tokens so order doesn't matter
    tokens = re.split(r"\s*[&,]\s*|\s+feat\.?\s+", s, flags=re.I)
    tokens = [t.strip() for t in tokens if t.strip()]
    return " & ".join(sorted(tokens)) if tokens else s


def normalize_track_text(s: str) -> str:
    """Remove common suffixes that differ between Shazam and filenames."""
    s = normalize(s)
    # Normalize (feat. X) to feat. X so Shazam and filename variants match
    s = re.sub(r"\s*\(feat\.\s+([^)]+)\)", r" feat. \1", s, flags=re.I)
    # Remove mix/remix/edit tags in parentheses or brackets (keep for fuzzy, but shorten)
    s = re.sub(r"\s*\([^)]*(?:mix|remix|edit|extended|radio|club|vocal|instrumental)[^)]*\)", "", s, flags=re.I)
    s = re.sub(r"\s*\[[^\]]*(?:mix|remix|edit|mixed)[^\]]*\]", "", s, flags=re.I)
    # Remove trailing punctuation
    s = re.sub(r"[\s\.\-_]+$", "", s)
    s = re.sub(r"^\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_shazam_line(line: str) -> Optional[Tuple[str, str]]:
    """Return (artist, title) or None."""
    line = line.strip()
    if not line:
        return None
    if " | " not in line:
        return None
    parts = line.split(" | ", 1)
    return (parts[0].strip(), parts[1].strip())


def parse_local_line(line: str) -> Optional[Tuple[str, str]]:
    """Return (artist, title) or None. Title is everything before .aiff/.mp3 etc."""
    line = line.strip()
    if not line:
        return None
    # Remove extension
    line = re.sub(r"\.(aiff|mp3|flac|m4a|wav)$", "", line, flags=re.I)
    if " - " not in line:
        return None
    parts = line.split(" - ", 1)
    return (parts[0].strip(), parts[1].strip())


def make_key(artist: str, title: str, strict: bool = False) -> str:
    """Create a comparable key. If strict, use raw normalize; else use track-normalized + artist order normalized."""
    a, t = normalize(artist), normalize(title)
    if not strict:
        a = normalize_artist(artist)
        t = normalize_track_text(title)
    return f"{a}|{t}"


def similarity(a: str, b: str) -> float:
    """Return similarity 0-100 (token_sort_ratio)."""
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b)


def load_shazam(path: str) -> List[Tuple[str, str, str]]:
    """List of (artist, title, key)."""
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = parse_shazam_line(line)
            if p:
                artist, title = p
                key = make_key(artist, title)
                out.append((artist, title, key))
    return out


def load_local(path: str) -> List[Tuple[str, str, str]]:
    """List of (artist, title, key)."""
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = parse_local_line(line)
            if p:
                artist, title = p
                key = make_key(artist, title)
                out.append((artist, title, key))
    return out


def main():
    base = Path(__file__).parent
    shazam_path = Path("/Users/keith/Downloads/shazam_tracks.txt")
    local_path = Path("/Users/keith/Downloads/local_filenames.txt")

    shazam = load_shazam(shazam_path)
    local = load_local(local_path)
    local_by_key = {r[2]: r for r in local}

    print(f"Shazam tracks: {len(shazam)}")
    print(f"Local files:   {len(local)}")
    print()

    # 1) Exact key match (normalized artist|title)
    exact = []
    for (artist, title, key) in shazam:
        if key in local_by_key:
            exact.append((key, (artist, title), local_by_key[key][:2]))

    print(f"Exact key matches (normalized artist|title): {len(exact)}")

    # 2) Fuzzy: use rapidfuzz process.extractOne for speed
    matched_keys = {r[2] for r in exact}
    # Build list of normalized local strings and keep index -> (la, lt, lk)
    local_norm_strings = []
    local_records = []
    for (la, lt, lk) in local:
        if lk in matched_keys:
            continue
        local_norm_strings.append(f"{normalize_artist(la)}|{normalize_track_text(lt)}")
        local_records.append((la, lt, lk))

    fuzzy_90 = []
    fuzzy_80 = []
    fuzzy_70 = []
    unmatched = []
    used_local_indices = set()

    for (artist, title, key) in shazam:
        if key in local_by_key:
            continue
        sk = f"{normalize_artist(artist)}|{normalize_track_text(title)}"
        # extractOne returns (best_match, score, index) from the list we pass
        result = process.extractOne(
            sk,
            local_norm_strings,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=70,
        )
        if result is None:
            unmatched.append((artist, title))
            continue
        best_str, best_ratio, idx = result
        # Avoid matching same local file to multiple Shazam tracks
        if idx in used_local_indices:
            unmatched.append((artist, title))
            continue
        best_local = local_records[idx]
        if best_ratio >= 90:
            fuzzy_90.append((best_ratio, (artist, title), best_local))
            used_local_indices.add(idx)
        elif best_ratio >= 80:
            fuzzy_80.append((best_ratio, (artist, title), best_local))
            used_local_indices.add(idx)
        else:
            fuzzy_70.append((best_ratio, (artist, title), best_local))
            used_local_indices.add(idx)

    print(f"Fuzzy matches 90%+: {len(fuzzy_90)}")
    print(f"Fuzzy matches 80-89%: {len(fuzzy_80)}")
    print(f"Fuzzy matches 70-79%: {len(fuzzy_70)}")
    print()

    total_matched = len(exact) + len(fuzzy_90) + len(fuzzy_80) + len(fuzzy_70)
    pct = 100 * total_matched / len(shazam) if shazam else 0
    print(f"Total matchable (exact + fuzzy 70%+): {total_matched} / {len(shazam)} = {pct:.1f}%")
    print(f"Unmatched Shazam tracks: {len(unmatched)}")
    print()

    # Summary at 80% threshold (your guess)
    at_80 = len(exact) + len(fuzzy_90) + len(fuzzy_80)
    pct_80 = 100 * at_80 / len(shazam) if shazam else 0
    print(f"At 80% threshold: {at_80} matches = {pct_80:.1f}%")

    # Show a few examples
    print("\n--- Sample exact matches ---")
    for (k, (a, t), (la, lt)) in exact[:5]:
        print(f"  Shazam: {a} | {t}")
        print(f"  Local:  {la} - {lt}")
        print()
    print("\n--- Sample fuzzy 80-89% ---")
    for (ratio, (a, t), (la, lt, _)) in fuzzy_80[:5]:
        print(f"  {ratio:.0f}%  Shazam: {a} | {t}")
        print(f"        Local:  {la} - {lt}")
        print()
    print("\n--- Sample unmatched ---")
    for (a, t) in unmatched[:10]:
        print(f"  {a} | {t}")

    # How many local files were never matched to any Shazam?
    matched_local_keys = {r[2] for r in exact}
    for (_, _, (_, _, lk)) in fuzzy_90 + fuzzy_80 + fuzzy_70:
        matched_local_keys.add(lk)
    unmatched_local_count = len(local) - len(matched_local_keys)
    print(f"\n--- Overlap ---")
    print(f"Local files never matched to any Shazam: {unmatched_local_count}")
    print(f"(So {len(matched_local_keys)} local files matched at least one Shazam track.)")


if __name__ == "__main__":
    main()
