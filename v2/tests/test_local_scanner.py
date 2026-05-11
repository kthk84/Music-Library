"""Tests for local_scanner module."""
import pytest
import os
from local_scanner import (
    normalize,
    parse_artist_title_from_filename,
    scan_folders,
    compute_to_download,
    _track_matches,
)


def test_normalize():
    """Normalize strings for matching."""
    assert normalize("  Hello  World  ") == "hello world"
    assert normalize("Artist - Song (Extended Mix)") == "artist - song"
    assert normalize("") == ""
    assert normalize("Original Mix") == ""


def test_parse_artist_title_from_filename():
    """Parse Artist - Title from filenames."""
    assert parse_artist_title_from_filename("Nova Nova - Prisoner Song.mp3") == (
        "Nova Nova", "Prisoner Song"
    )
    assert parse_artist_title_from_filename("Wu Tang – Bring Me Da Ruckus.mp3") == (
        "Wu Tang", "Bring Me Da Ruckus"
    )
    assert parse_artist_title_from_filename("SoloTrack.mp3") == ("", "SoloTrack")


def test_parse_artist_title_single_hyphen():
    """Clean filenames with single hyphen: Artist-Title.ext."""
    assert parse_artist_title_from_filename("Dua Lipa-Levitate.aiff") == (
        "Dua Lipa", "Levitate"
    )
    assert parse_artist_title_from_filename("Artist-Title.mp3") == ("Artist", "Title")


def test_scan_folders_empty(tmp_path, monkeypatch):
    """Scan empty folder returns empty list."""
    def mock_get_metadata(fp):
        return {"artist": "", "title": ""}
    monkeypatch.setattr("local_scanner._get_audio_metadata", mock_get_metadata)
    result = scan_folders([str(tmp_path)])
    assert result == []


def test_scan_folders_with_files(tmp_path, monkeypatch):
    """Scan folder with MP3s returns track info."""
    (tmp_path / "track1.mp3").write_bytes(b"x")
    (tmp_path / "track2.mp3").write_bytes(b"y")
    (tmp_path / "not_mp3.txt").write_text("ignored")

    call_count = [0]
    def mock_get_metadata(fp):
        call_count[0] += 1
        if "track1" in fp:
            return {"artist": "Artist A", "title": "Song 1"}
        if "track2" in fp:
            return {"artist": "", "title": ""}
        return {"artist": "", "title": ""}
    monkeypatch.setattr("local_scanner._get_audio_metadata", mock_get_metadata)

    result = scan_folders([str(tmp_path)])
    assert len(result) == 2
    with_tags = [r for r in result if r["from_tags"]]
    assert len(with_tags) == 1
    assert with_tags[0]["artist"] == "Artist A" and with_tags[0]["title"] == "Song 1"
    # track2 has no tags -> parsed from filename
    no_tags = [r for r in result if not r["from_tags"]]
    assert len(no_tags) == 1
    assert no_tags[0]["filename"] == "track2.mp3"


def test_compute_to_download_all_missing():
    """All Shazam tracks missing locally."""
    shazam = [
        {"artist": "Nova Nova", "title": "Prisoner Song"},
        {"artist": "Wu Tang", "title": "Bring Me Da Ruckus"},
    ]
    local = [
        {"artist": "Other", "title": "Other Song", "filename": "other.mp3"},
    ]
    to_dl, _, _, _ = compute_to_download(shazam, local)
    assert len(to_dl) == 2


def test_compute_to_download_all_found():
    """All Shazam tracks found locally."""
    shazam = [
        {"artist": "Nova Nova", "title": "Prisoner Song"},
    ]
    local = [
        {"artist": "Nova Nova", "title": "Prisoner Song", "filename": "nova.mp3"},
    ]
    to_dl, _, _, _ = compute_to_download(shazam, local)
    assert len(to_dl) == 0


def test_compute_to_download_partial():
    """Some found, some missing."""
    shazam = [
        {"artist": "Found", "title": "Song"},
        {"artist": "Missing", "title": "Track"},
    ]
    local = [
        {"artist": "Found", "title": "Song", "filename": "found.mp3"},
    ]
    to_dl, _, _, _ = compute_to_download(shazam, local)
    assert len(to_dl) == 1
    assert to_dl[0]["artist"] == "Missing"


def test_track_matches_normalized():
    """Matching works with normalized strings (suffix stripped on both sides)."""
    local = [{"artist": "Nova Nova", "title": "Prisoner Song (Extended Mix)", "filename": "x.mp3"}]
    assert _track_matches({"artist": "Nova Nova", "title": "Prisoner Song"}, local) is True


def test_track_matches_normalized_local_artist_title():
    """Fuzzy match compares normalized local so 'Title (Original Mix)' matches Shazam 'Title'."""
    local = [
        {"artist": "Some Artist", "title": "Back Together (Original Mix)", "filename": "x.aiff", "filepath": "/music/x.aiff"}
    ]
    assert _track_matches({"artist": "Some Artist", "title": "Back Together"}, local) is True

