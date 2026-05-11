"""Tests for shazam_reader module."""
import pytest
import os
from shazam_reader import get_shazam_tracks, _find_shazam_db


def test_get_shazam_tracks_with_mock_db(mock_shazam_db):
    """Extract tracks from mock Shazam database."""
    tracks = get_shazam_tracks(db_path=mock_shazam_db)
    assert len(tracks) == 3
    # Ordered by ZDATE DESC (newest first): 1000002, 1000001, 1000000
    by_artist = {t["artist"]: t for t in tracks}
    assert by_artist["Nova Nova"]["title"] == "Prisoner Song (Extended Original Mix)"
    assert by_artist["Nova Nova"]["shazamed_at"] == 978307200 + 1000000
    assert by_artist["Wu Tang Clan"]["title"] == "Bring Me Da Ruckus"
    assert by_artist["Eazy-E"]["title"] == "Real Muthaphuckkin's G's"


def test_shazam_tracks_deduplication(mock_shazam_db):
    """Duplicate artist+title should be deduplicated."""
    conn = __import__("sqlite3").connect(mock_shazam_db)
    conn.execute(
        "INSERT INTO ZSHTAGRESULTMO (Z_PK, ZTRACKNAME, ZDATE) VALUES (4, 'Prisoner Song (Extended Original Mix)', 1000003)"
    )
    conn.execute("INSERT INTO ZSHARTISTMO (Z_PK, ZNAME, ZTAGRESULT) VALUES (4, 'Nova Nova', 4)")
    conn.commit()
    conn.close()

    tracks = get_shazam_tracks(db_path=mock_shazam_db)
    # Should still be 3 unique tracks (Nova Nova duplicate excluded)
    nova_tracks = [t for t in tracks if t["artist"] == "Nova Nova"]
    assert len(nova_tracks) == 1


def test_shazam_db_not_found(monkeypatch):
    """Missing DB raises FileNotFoundError with helpful message."""
    def mock_find(_override=None):
        return None  # Simulate DB not found anywhere
    monkeypatch.setattr("shazam_reader._find_shazam_db", mock_find)
    with pytest.raises(FileNotFoundError) as exc_info:
        get_shazam_tracks(db_path="/nonexistent/path/to/shazam.sqlite")
    assert "Shazam database not found" in str(exc_info.value)


def test_find_shazam_db_with_override(mock_shazam_db):
    """Override path works when file exists."""
    found = _find_shazam_db(override_path=mock_shazam_db)
    assert found is not None
    assert found[0] == mock_shazam_db
    assert found[1] in ("library", "datamodel")


def test_find_shazam_db_override_nonexistent(monkeypatch):
    """Override with nonexistent path falls through; returns None if no DB anywhere."""
    # Ensure default Shazam paths don't exist
    monkeypatch.setattr("os.path.exists", lambda p: False)
    monkeypatch.setattr("os.listdir", lambda _: [])
    found = _find_shazam_db(override_path="/nonexistent/db.sqlite")
    assert found is None
