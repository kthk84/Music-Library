#!/usr/bin/env python3
"""
Scan 2023/2024/2025/2026 AIFF folders:
1. Check if all files from local_filenames.txt log are present on disk.
2. Extract metadata for all tracks (duration + tags if any).
Then optionally run the Shazam vs local comparison again.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Folders to scan (exact names as on disk)
MUSIC_BASE = Path("/Users/keith/Desktop/Music")
AIFF_FOLDERS = [
    MUSIC_BASE / "2023 aiff",
    MUSIC_BASE / "2024 aiff",
    MUSIC_BASE / "2025 aiff",
    MUSIC_BASE / "2026 AIFF",
]
LOG_PATH = Path("/Users/keith/Downloads/local_filenames.txt")


def find_all_aiff_files() -> Dict[str, Path]:
    """Return dict: basename -> full path (first occurrence). Case-insensitive key for matching."""
    seen_basenames: Set[str] = set()
    result: Dict[str, Path] = {}
    for folder in AIFF_FOLDERS:
        if not folder.exists():
            print(f"Warning: folder does not exist: {folder}")
            continue
        for p in folder.rglob("*.aiff"):
            if not p.is_file():
                continue
            base = p.name
            key = base.lower()
            if key not in seen_basenames:
                seen_basenames.add(key)
                result[key] = p
    return result


def load_log_filenames() -> List[str]:
    """Return list of basenames from log (one per line, stripped)."""
    if not LOG_PATH.exists():
        print(f"Error: log not found: {LOG_PATH}")
        return []
    names = []
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            name = line.strip()
            if name:
                names.append(name)
    return names


def check_log_files_on_disk(
    log_names: List[str],
    disk_files: Dict[str, Path],
) -> Tuple[List[str], List[str]]:
    """Return (found_list, missing_list)."""
    found = []
    missing = []
    for name in log_names:
        if not name.lower().endswith(".aiff"):
            # log might have other extensions
            key = (name + ".aiff").lower() if "." not in name else name.lower()
        else:
            key = name.lower()
        if key in disk_files:
            found.append(name)
        else:
            missing.append(name)
    return found, missing


def extract_metadata_afinfo(filepath: Path) -> Optional[Dict]:
    """Use macOS afinfo to get duration and format. Returns dict or None."""
    try:
        out = subprocess.run(
            ["afinfo", str(filepath)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return None
        info = {"path": str(filepath), "duration_sec": None, "channels": None, "sample_rate": None}
        for line in out.stdout.splitlines():
            if "estimated duration:" in line:
                try:
                    info["duration_sec"] = float(line.split(":", 1)[1].strip().replace(" sec", ""))
                except (IndexError, ValueError):
                    pass
            elif "Data format:" in line or "format:" in line.lower():
                # e.g. "2 ch,  44100 Hz"
                if "ch," in line:
                    parts = line.split(",")
                    for p in parts:
                        p = p.strip()
                        if p.endswith("ch") and info["channels"] is None:
                            try:
                                info["channels"] = int(p.replace("ch", "").strip())
                            except ValueError:
                                pass
                        if "Hz" in p and info["sample_rate"] is None:
                            try:
                                info["sample_rate"] = int(p.replace("Hz", "").strip())
                            except ValueError:
                                pass
        return info
    except Exception as e:
        return {"path": str(filepath), "error": str(e)}


def extract_metadata_mutagen(filepath: Path) -> Optional[Dict]:
    """Use mutagen to get duration and tags if available."""
    try:
        from mutagen import File as MFile
        from mutagen.aiff import AIFF
    except ImportError:
        return None
    try:
        f = MFile(str(filepath))
        if f is None:
            return None
        info = {"path": str(filepath), "duration_sec": getattr(f.info, "length", None)}
        if hasattr(f, "tags") and f.tags is not None:
            tags = dict(f.tags)
            for k in ("artist", "title", "album", "genre"):
                if k in tags:
                    info[k] = str(tags[k][0]) if isinstance(tags[k], (list, tuple)) else str(tags[k])
        return info
    except Exception as e:
        return {"path": str(filepath), "error": str(e)}


def main():
    print("=== Scanning AIFF folders ===\n")
    for d in AIFF_FOLDERS:
        print(f"  {d}  exists={d.exists()}")
    print()

    disk_files = find_all_aiff_files()
    print(f"Total .aiff files found in folders: {len(disk_files)}")

    log_names = load_log_filenames()
    print(f"Entries in log ({LOG_PATH.name}): {len(log_names)}")

    found, missing = check_log_files_on_disk(log_names, disk_files)
    log_aiff = [n for n in log_names if n.lower().endswith(".aiff")]
    missing_aiff = [n for n in missing if n.lower().endswith(".aiff")]
    other_ext = [n for n in missing if not n.lower().endswith(".aiff")]

    print(f"\nLog files FOUND on disk: {len(found)}")
    print(f"Log entries that are .aiff: {len(log_aiff)}")
    print(f"  -> of those, missing from AIFF folders: {len(missing_aiff)}")
    print(f"Log entries with other extensions (.mp3/.wav/.aif) not in AIFF folders: {len(other_ext)}")
    if missing_aiff:
        print("\nMissing .aiff from log:")
        for name in missing_aiff[:20]:
            print(f"  {name}")
    if other_ext:
        print("\nOther extensions (expected not in AIFF folders):")
        for name in other_ext[:15]:
            print(f"  {name}")
        if len(other_ext) > 15:
            print(f"  ... and {len(other_ext) - 15} more")

    # Metadata: use afinfo (no extra deps) for all; optionally mutagen for tags
    print("\n=== Extracting metadata (afinfo) ===\n")
    meta_ok = 0
    meta_fail = 0
    paths_to_scan = [disk_files[name.lower()] for name in found if name.lower() in disk_files]
    total = len(paths_to_scan)
    for i, path in enumerate(paths_to_scan):
        if (i + 1) % 500 == 0:
            print(f"  ... {i + 1}/{total}")
        meta = extract_metadata_afinfo(path)
        if meta and meta.get("duration_sec") is not None:
            meta_ok += 1
        else:
            meta_fail += 1

    print(f"Metadata extracted (duration): {meta_ok}")
    print(f"Metadata failed: {meta_fail}")

    # Try mutagen on first few to report if tags exist
    try:
        from mutagen import File as MFile
        has_mutagen = True
    except ImportError:
        has_mutagen = False
    if has_mutagen and paths_to_scan:
        tagged = 0
        for path in paths_to_scan[:100]:
            m = extract_metadata_mutagen(path)
            if m and m.get("artist") or m.get("title"):
                tagged += 1
        print(f"\nMutagen: in first 100 files, {tagged} had artist/title tags in file.")

    print("\n=== Done. Run compare_tracks.py for Shazam vs local test. ===")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
