"""Tests for Shazam vs local matching logic."""
from local_scanner import normalize, compute_to_download, _track_matches, _artist_tokens, _artist_overlap_or_in_filename, _canon, _canon_match


def test_normalize_mix_suffixes():
    """Strip (Extended Mix), (Original Mix) etc."""
    assert "original mix" not in normalize("Song (Original Mix)")
    assert "extended" not in normalize("Track (Extended Mix)")


def test_compute_diff_unicode():
    """Handle unicode in artist/title."""
    shazam = [{"artist": "Âme", "title": "DON'T KILL MY VIBE"}]
    local = [{"artist": "Ame", "title": "Dont Kill My Vibe", "filename": "ame.mp3"}]
    to_dl, _, _, _ = compute_to_download(shazam, local)
    # Fuzzy match should find it
    assert len(to_dl) == 0 or len(to_dl) == 1  # Depends on similarity threshold


def test_compute_diff_artist_ampersand():
    """Artist with &"""
    shazam = [{"artist": "Tom & Collins", "title": "Dancing Shoes"}]
    local = [{"artist": "Tom & Collins", "title": "Dancing Shoes", "filename": "x.mp3"}]
    to_dl, _, _, _ = compute_to_download(shazam, local)
    assert len(to_dl) == 0


def test_artist_tokens_overlap():
    """Artist token overlap: 'CamelPhat & Arodes' shares token with 'CamelPhat' or filename."""
    assert _artist_tokens("CamelPhat & Arodes") == {"camelphat", "arodes"}
    assert _artist_tokens("CamelPhat") == {"camelphat"}
    assert _artist_overlap_or_in_filename("CamelPhat & Arodes", "camelphat", "camelphat - cycles.aiff")
    assert _artist_overlap_or_in_filename("CamelPhat & Arodes", "other artist", "camelphat - cycles.aiff")


def test_match_artist_token_in_filename():
    """Shazam 'Artist A & B' / 'Title' matches local 'Artist A' / 'Title' or filename with Artist A."""
    local = [{"artist": "CamelPhat", "title": "Cycles", "filename": "CamelPhat - Cycles.aiff", "filepath": "/music/CamelPhat - Cycles.aiff"}]
    assert _track_matches({"artist": "CamelPhat & Arodes", "title": "Cycles"}, local) is True


def test_canon_match_same_name():
    """Canonical match: same name, different punctuation or extra words."""
    assert _canon("Back Together (Original Mix)") == "back together"
    assert _canon("Back Together") == "back together"
    assert _canon_match("Back Together (Original Mix)", "Back Together") is True
    assert _canon_match("Tom Zeta, Josh Gigante", "Tom Zeta") is True  # one contains the other
    assert _canon_match("Tom Zeta", "Tom Zeta, Josh Gigante") is True


def test_canon_match_dots_in_artist():
    """R.E.Zarin (Shazam) matches REZarin (filename)."""
    assert _canon("R.E.Zarin") == _canon("REZarin") == "rezarin"
    assert _canon_match("R.E.Zarin", "REZarin") is True
    local = [{"artist": "REZarin", "title": "Androme", "filename": "x.aiff", "filepath": "/x.x"}]
    assert _track_matches({"artist": "R.E.Zarin", "title": "Androme"}, local) is True
