"""
Extract Shazam library tracks from local macOS SQLite database.
Supports two Shazam storage locations:
1. ShazamLibrary.sqlite (com.apple.shazamd) - primary, has ZSHTRACKMO
2. ShazamDataModel.sqlite (Group Containers) - legacy, has ZSHARTISTMO + ZSHTAGRESULTMO

Core Data ZDATE is seconds since 2001-01-01. Add 978307200 for Unix timestamp.
"""
import os
import sqlite3
from typing import List, Dict, Optional, Any

# Core Data reference date (2001-01-01 00:00:00 UTC) in Unix seconds
_COREDATA_EPOCH = 978307200

# Primary: ShazamLibrary (macOS Shazam daemon - used by menu bar / Music recognition)
SHAZAM_LIBRARY_PATH = os.path.expanduser(
    "~/Library/Application Support/com.apple.shazamd/ShazamLibrary.sqlite"
)

# Legacy: ShazamDataModel (older Shazam Mac app)
SHAZAM_DATAMODEL_PATHS = [
    os.path.expanduser(
        "~/Library/Containers/com.shazam.mac.Shazam/Data/Documents/ShazamDataModel.sqlite"
    ),
]


def _find_shazam_db(override_path: Optional[str] = None) -> Optional[tuple]:
    """
    Locate Shazam SQLite database.
    Returns (path, "library"|"datamodel") or None if not found.
    """
    if override_path and os.path.exists(override_path):
        return (override_path, "library" if "ShazamLibrary" in override_path else "datamodel")

    # Primary: ShazamLibrary (has ZSHTRACKMO with ZTITLE, ZSUBTITLE)
    if os.path.exists(SHAZAM_LIBRARY_PATH):
        return (SHAZAM_LIBRARY_PATH, "library")

    # Legacy: ShazamDataModel in Group Containers
    group_prefix = os.path.expanduser("~/Library/Group Containers/")
    if os.path.exists(group_prefix):
        try:
            for child in os.listdir(group_prefix):
                if not child.endswith(".group.com.shazam"):
                    continue
                db_path = os.path.join(
                    group_prefix, child, "com.shazam.mac.Shazam/ShazamDataModel.sqlite"
                )
                if os.path.exists(db_path):
                    return (db_path, "datamodel")
        except OSError:
            pass

    # Legacy: Containers path
    for path in SHAZAM_DATAMODEL_PATHS:
        if os.path.exists(path):
            return (path, "datamodel")

    return None


def get_shazam_tracks(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extract all tagged tracks from Shazam macOS app.
    Returns list of {"artist": str, "title": str, "shazamed_at": int?}.
    shazamed_at is Unix timestamp (seconds) when track was tagged; None if unavailable.
    Raises FileNotFoundError if DB not found.
    """
    found = _find_shazam_db(db_path)
    if not found:
        raise FileNotFoundError(
            "Shazam database not found. Ensure Shazam for Mac is installed and has tagged tracks. "
            "Looked in: ~/Library/Application Support/com.apple.shazamd/ShazamLibrary.sqlite "
            "and ~/Library/Group Containers/*.group.com.shazam/"
        )

    path, db_type = found
    connection = sqlite3.connect(path)
    connection.text_factory = str
    cursor = connection.cursor()

    try:
        if db_type == "library":
            # ShazamLibrary: ZSHTRACKMO has ZTITLE (track), ZSUBTITLE (artist), ZDATE
            try:
                cursor.execute(
                    """
                    SELECT ZSUBTITLE, ZTITLE, ZDATE
                    FROM ZSHTRACKMO
                    WHERE (ZTITLE IS NOT NULL AND ZTITLE != '') OR (ZSUBTITLE IS NOT NULL AND ZSUBTITLE != '')
                    ORDER BY ZDATE DESC
                    """
                )
            except sqlite3.OperationalError:
                # ZDATE may not exist in some Shazam versions
                cursor.execute(
                    """
                    SELECT ZSUBTITLE, ZTITLE
                    FROM ZSHTRACKMO
                    WHERE (ZTITLE IS NOT NULL AND ZTITLE != '') OR (ZSUBTITLE IS NOT NULL AND ZSUBTITLE != '')
                    """
                )
            rows = cursor.fetchall()
        else:
            # ShazamDataModel: ZSHARTISTMO + ZSHTAGRESULTMO
            try:
                cursor.execute(
                    """
                    SELECT artist.ZNAME, tag.ZTRACKNAME, tag.ZDATE
                    FROM ZSHARTISTMO artist, ZSHTAGRESULTMO tag
                    WHERE artist.ZTAGRESULT = tag.Z_PK
                    ORDER BY tag.ZDATE DESC
                    """
                )
            except sqlite3.OperationalError:
                cursor.execute(
                    """
                    SELECT artist.ZNAME, tag.ZTRACKNAME
                    FROM ZSHARTISTMO artist, ZSHTAGRESULTMO tag
                    WHERE artist.ZTAGRESULT = tag.Z_PK
                    """
                )
            rows = cursor.fetchall()
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"Shazam database schema may have changed. Error: {e}") from e
    finally:
        connection.close()

    tracks = []
    seen = set()
    for row in rows:
        artist_name, track_name = row[0], row[1]
        zdate = row[2] if len(row) > 2 else None
        artist = (artist_name or "").strip()
        title = (track_name or "").strip()
        if not artist and not title:
            continue
        key = (artist.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        shazamed_at = None
        if zdate is not None:
            try:
                shazamed_at = int(_COREDATA_EPOCH + float(zdate))
            except (TypeError, ValueError):
                pass
        tracks.append({"artist": artist, "title": title, "shazamed_at": shazamed_at})

    return tracks
