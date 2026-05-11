"""
Regression tests for Shazam/Sync cover art: API must serve cached files and
status JSON must retain cover_hashes whenever we merge from the saved cache.

Without cover_hashes in /api/shazam-sync/status, the UI has nothing to point
background-image URLs at (thumbnails stay blank even when files exist).
"""
import pytest


# Valid 32-char hex (md5) used as cover cache filename stem
SAMPLE_HASH = "abcd1234abcd1234abcd1234abcd1234"
TRACK_KEY = "Test Artist - Test Title"


@pytest.fixture
def app_module():
    """Import app lazily so monkeypatch applies before heavy routes run."""
    import app as m

    return m


def test_shazam_sync_cover_serves_jpeg_from_cache_dir(app_module, monkeypatch, tmp_path):
    """GET /api/shazam-sync/cover/<hash> returns file bytes when present on disk."""
    cache_dir = tmp_path / "cover_cache"
    cache_dir.mkdir()
    (cache_dir / f"{SAMPLE_HASH}.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9")

    monkeypatch.setattr(app_module, "_get_cover_cache_dir", lambda: str(cache_dir))

    client = app_module.app.test_client()
    resp = client.get(f"/api/shazam-sync/cover/{SAMPLE_HASH}")
    assert resp.status_code == 200, resp.data
    assert resp.mimetype == "image/jpeg"
    assert len(resp.data) > 0


def test_shazam_sync_cover_404_when_missing(app_module, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_cover_cache_dir", lambda: str(tmp_path / "empty_cache"))
    (tmp_path / "empty_cache").mkdir()

    client = app_module.app.test_client()
    resp = client.get(f"/api/shazam-sync/cover/{SAMPLE_HASH}")
    assert resp.status_code == 404
    assert "no-store" in resp.headers.get("Cache-Control", "").lower()


def test_shazam_sync_cover_404_emits_debug_log(app_module, monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setattr(app_module, "_get_cover_cache_dir", lambda: str(tmp_path / "empty_cache2"))
    (tmp_path / "empty_cache2").mkdir()
    client = app_module.app.test_client()
    with caplog.at_level(logging.DEBUG):
        client.get(f"/api/shazam-sync/cover/{SAMPLE_HASH}")
    assert "shazam_sync_cover: no file for hash" in caplog.text


def test_rebuild_status_no_folders_preserves_cover_hashes(app_module, monkeypatch):
    """_rebuild_status_from_caches (no destination folders) must copy cover_hashes from old file."""
    old = {
        "urls": {TRACK_KEY: "https://soundeo.com/track-1.html"},
        "cover_hashes": {TRACK_KEY: SAMPLE_HASH},
        "not_found": {},
        "search_outcomes": [],
    }

    def fake_shazam():
        return [{"artist": "Test Artist", "title": "Test Title", "shazamed_at": 1}]

    monkeypatch.setattr("shazam_cache.load_shazam_cache", fake_shazam)
    monkeypatch.setattr("shazam_cache.load_status_cache", lambda: old)
    monkeypatch.setattr("shazam_cache.load_skip_list", lambda: [])
    monkeypatch.setattr("config_shazam.get_destination_folders", lambda: [])

    out = app_module._rebuild_status_from_caches()
    assert out is not None
    assert out.get("cover_hashes", {}).get(TRACK_KEY) == SAMPLE_HASH


def test_rebuild_status_after_compare_preserves_cover_hashes(app_module, monkeypatch, tmp_path):
    """Full compare rebuild path merges old cover_hashes alongside urls/starred."""
    # Minimal local scan the matcher can use
    local_scan = {
        "folder_paths": [str(tmp_path)],
        "tracks": [],
        "updated_at": "2099-01-01T00:00:00Z",
    }
    old = {
        "cover_hashes": {TRACK_KEY: SAMPLE_HASH},
        "urls": {TRACK_KEY: "https://example.com/t"},
    }

    def fake_shazam():
        return [{"artist": "Test Artist", "title": "Test Title"}]

    monkeypatch.setattr("shazam_cache.load_shazam_cache", fake_shazam)
    monkeypatch.setattr("shazam_cache.load_local_scan_cache", lambda: dict(local_scan))
    monkeypatch.setattr("shazam_cache.load_status_cache", lambda: old)
    monkeypatch.setattr("shazam_cache.load_skip_list", lambda: [])
    monkeypatch.setattr("shazam_cache.local_scan_cache_valid", lambda scan, folders: True)
    monkeypatch.setattr("config_shazam.get_destination_folders", lambda: [str(tmp_path)])

    # Avoid heavy matching: return everything as to_download with empty local tracks
    def fake_compute(shazam_tracks, local_tracks):
        to_dl = [
            {"artist": t["artist"], "title": t["title"]}
            for t in shazam_tracks
        ]
        return to_dl, {}, {}, {}

    monkeypatch.setattr("local_scanner.compute_to_download", fake_compute)
    monkeypatch.setattr("local_scanner._find_matching_local_track", lambda *a, **k: (None, None))

    out = app_module._rebuild_status_from_caches()
    assert out is not None
    assert out.get("cover_hashes", {}).get(TRACK_KEY) == SAMPLE_HASH


def test_get_best_available_partial_status_preserves_cover_hashes(app_module, monkeypatch):
    """
    When there is no usable compare snapshot (empty status file) but Shazam tracks
    exist, we build a 'partial' status and merge from the last saved file. That
    merge must include cover_hashes (same as urls).
    """
    old = {
        "urls": {TRACK_KEY: "https://soundeo.com/x"},
        "cover_hashes": {TRACK_KEY: SAMPLE_HASH},
        "search_outcomes": [],
    }
    load_calls = {"n": 0}

    def fake_load_status():
        load_calls["n"] += 1
        # First read: no compare payload → triggers partial + Shazam branch
        if load_calls["n"] == 1:
            return {}
        return dict(old)

    monkeypatch.setattr("shazam_cache.load_status_cache", fake_load_status)
    monkeypatch.setattr("shazam_cache.load_shazam_cache", lambda: [{"artist": "Test Artist", "title": "Test Title"}])
    monkeypatch.setattr(app_module, "_rebuild_status_from_caches", lambda: None)
    monkeypatch.setattr("shazam_cache.save_status_cache", lambda _s: None)

    app_module.app._shazam_sync_status = None

    out = app_module._get_best_available_status()
    assert out.get("cover_hashes", {}).get(TRACK_KEY) == SAMPLE_HASH
