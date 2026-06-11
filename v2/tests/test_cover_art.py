"""
Regression tests for Shazam/Sync cover art.

The recurring "covers vanished" bug had ONE root cause: ``cover_hashes`` (track
key -> md5) was treated as precious state hand-threaded through ~4 server
rebuild paths and ~4 client merge paths; any interruption / stale-cache fetch /
cancel-race wrote a status without it and thumbnails went blank even though the
files were on disk. The permanent fix makes the on-disk ``cover_cache/``
directory the source of truth: covers are named ``<md5(key_variant)>.jpg``, the
map is recomputed from disk on every ``/status`` read (so it can never go
sparse), and the UI fetches covers BY KEY so the server resolves the file via
the canonical variant set.

These tests pin that invariant. The headline one,
``test_status_recomputes_cover_hashes_from_disk_when_persisted_map_empty``,
reproduces the exact post-interruption state (persisted ``cover_hashes: {}``,
files present on disk) and asserts ``/status`` heals it — it would have failed
on every one of the 7 prior band-aid commits.
"""
import hashlib
import json

import pytest


# Valid 32-char hex (md5) used as cover cache filename stem
SAMPLE_HASH = "abcd1234abcd1234abcd1234abcd1234"
TRACK_KEY = "Test Artist - Test Title"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


# 20-byte valid JPEG (SOI ... EOI) so send_file has real bytes to serve.
_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _write_cover(cache_dir, key: str, ext: str = ".jpg") -> str:
    """Write a cover file named md5(key)+ext into cache_dir. Returns the hash."""
    h = _md5(key)
    (cache_dir / f"{h}{ext}").write_bytes(_JPEG_BYTES)
    return h


@pytest.fixture
def app_module():
    """Import app lazily so monkeypatch applies before heavy routes run."""
    import app as m

    return m


@pytest.fixture
def cover_dir(tmp_path, monkeypatch):
    """A fresh cover_cache dir, wired into BOTH app and lib.covers namespaces."""
    import app as app_module
    import lib.covers as covers

    cache_dir = tmp_path / "cover_cache"
    cache_dir.mkdir()
    # find_cover_file_for_key / compute_cover_hashes_from_disk resolve
    # _get_cover_cache_dir in lib.covers; the /cover/<hash> route resolves it in
    # app's namespace. Patch both so every path points at the test dir.
    monkeypatch.setattr(covers, "_get_cover_cache_dir", lambda: str(cache_dir))
    monkeypatch.setattr(app_module, "_get_cover_cache_dir", lambda: str(cache_dir))
    return cache_dir


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


# =====================================================================
# Canonical variant normalizer — the single source of truth for keys.
# =====================================================================

def test_cover_key_variants_covers_case_parens_accents():
    from lib.covers import cover_key_variants

    variants = cover_key_variants("Davi & Definition - Désolé (Original Mix)")
    # exact + lowercase present
    assert "Davi & Definition - Désolé (Original Mix)" in variants
    assert "davi & definition - désolé (original mix)" in variants
    # parens stripped
    assert any("désolé" in v and "(" not in v for v in variants)
    # deep-normalized: accents folded, '&'->',', artists sorted, lowercase
    assert "davi, definition - desole" in variants
    # ordered + de-duplicated (no repeats)
    assert len(variants) == len(set(variants))


def test_cover_key_variants_empty_key():
    from lib.covers import cover_key_variants
    assert cover_key_variants("") == []
    assert cover_key_variants("   ") == []


# =====================================================================
# find_cover_file_for_key + the by-key endpoint (resilient fetch path)
# =====================================================================

def test_find_cover_file_for_key_exact(cover_dir):
    from lib.covers import find_cover_file_for_key
    _write_cover(cover_dir, TRACK_KEY)
    found = find_cover_file_for_key(TRACK_KEY)
    assert found is not None
    fp, mime = found
    assert fp.endswith(f"{_md5(TRACK_KEY)}.jpg")
    assert mime == "image/jpeg"


def test_find_cover_file_for_key_resolves_variant(cover_dir):
    """Cover cached under the deep-normalized variant is found from the display key."""
    from lib.covers import find_cover_file_for_key
    display_key = "Davi & Definition - Désolé"
    stored_variant = "davi, definition - desole"   # deep-norm form
    _write_cover(cover_dir, stored_variant)
    found = find_cover_file_for_key(display_key)
    assert found is not None, "variant resolution failed — display key should find the deep-norm file"
    assert found[0].endswith(f"{_md5(stored_variant)}.jpg")


def test_find_cover_file_for_key_png(cover_dir):
    from lib.covers import find_cover_file_for_key
    _write_cover(cover_dir, TRACK_KEY, ext=".png")
    found = find_cover_file_for_key(TRACK_KEY)
    assert found is not None and found[1] == "image/png"


def test_cover_by_key_endpoint_serves_bytes(app_module, cover_dir):
    _write_cover(cover_dir, TRACK_KEY)
    client = app_module.app.test_client()
    resp = client.get("/api/shazam-sync/cover-by-key", query_string={"key": TRACK_KEY})
    assert resp.status_code == 200, resp.data
    assert resp.mimetype == "image/jpeg"
    assert resp.data == _JPEG_BYTES
    assert "immutable" in resp.headers.get("Cache-Control", "")


def test_cover_by_key_endpoint_resolves_accented_variant(app_module, cover_dir):
    """The exact UX bug: row key has accents/&, file cached under the folded form."""
    _write_cover(cover_dir, "davi, definition - desole")
    client = app_module.app.test_client()
    resp = client.get("/api/shazam-sync/cover-by-key", query_string={"key": "Davi & Definition - Désolé"})
    assert resp.status_code == 200, resp.data
    assert resp.data == _JPEG_BYTES


def test_cover_by_key_resolves_parens_and_apostrophe_keys(app_module, cover_dir):
    """Keys with ()/' must round-trip when percent-encoded (the CSS-url regression:
    encodeURIComponent leaves !'()* raw, which breaks CSS url() embedding; the
    frontend now percent-encodes them — server must decode them identically)."""
    key = "Robin S. - Show Me Love (Stone's Club Mix)"
    _write_cover(cover_dir, key)
    client = app_module.app.test_client()
    # Fully percent-encoded form, exactly as shazamCoverByKeyUrl produces it.
    from urllib.parse import quote
    encoded = quote(key, safe="")
    resp = client.get(f"/api/shazam-sync/cover-by-key?key={encoded}")
    assert resp.status_code == 200, resp.data
    assert resp.data == _JPEG_BYTES


def test_cover_by_key_404_when_missing(app_module, cover_dir):
    client = app_module.app.test_client()
    resp = client.get("/api/shazam-sync/cover-by-key", query_string={"key": "No Such - Track"})
    assert resp.status_code == 404
    assert "no-store" in resp.headers.get("Cache-Control", "").lower()


def test_cover_by_key_400_empty_or_oversized(app_module, cover_dir):
    client = app_module.app.test_client()
    assert client.get("/api/shazam-sync/cover-by-key", query_string={"key": ""}).status_code == 400
    assert client.get("/api/shazam-sync/cover-by-key", query_string={"key": "x" * 600}).status_code == 400


def test_cover_by_key_path_traversal_is_safe(app_module, cover_dir):
    """A traversal-looking key hashes to hex, so it can never escape cover_cache."""
    client = app_module.app.test_client()
    resp = client.get("/api/shazam-sync/cover-by-key", query_string={"key": "../../../../etc/passwd"})
    # No cover file for that md5 → clean 404, never a file outside the cache dir.
    assert resp.status_code == 404


# =====================================================================
# Disk-derivation — the read-path source of truth.
# =====================================================================

def test_compute_cover_hashes_from_disk_keys_by_primary(cover_dir):
    from lib.covers import compute_cover_hashes_from_disk
    h = _write_cover(cover_dir, TRACK_KEY)
    status = {"to_download": [{"artist": "Test Artist", "title": "Test Title"}]}
    out = compute_cover_hashes_from_disk(status)
    # primary key + lowercase both present and correct
    assert out.get(TRACK_KEY) == h
    assert out.get(TRACK_KEY.lower()) == h


def test_compute_cover_hashes_from_disk_from_urls(cover_dir):
    from lib.covers import compute_cover_hashes_from_disk
    h = _write_cover(cover_dir, TRACK_KEY)
    out = compute_cover_hashes_from_disk({"urls": {TRACK_KEY: "https://soundeo.com/x"}})
    assert out.get(TRACK_KEY) == h


def test_compute_cover_hashes_empty_when_no_files(cover_dir):
    from lib.covers import compute_cover_hashes_from_disk
    out = compute_cover_hashes_from_disk({"urls": {TRACK_KEY: "https://x"}})
    assert out == {}


# =====================================================================
# HEADLINE REGRESSION: /status recomputes a complete cover_hashes map
# from disk even when the persisted/in-memory map is empty.
# This reproduces the post-interruption state and proves the bug class
# is closed at the read chokepoint. Would FAIL on all 7 prior commits.
# =====================================================================

def test_status_recomputes_cover_hashes_from_disk_when_persisted_map_empty(app_module, cover_dir, monkeypatch):
    h = _write_cover(cover_dir, TRACK_KEY)

    # Simulate a status that lost its cover_hashes (interrupted write / cancel
    # race / stale-cache fetch) but whose track + url survive and whose cover
    # file is sitting on disk.
    broken_status = {
        "cover_hashes": {},                       # <- wiped
        "urls": {TRACK_KEY: "https://soundeo.com/x"},
        "to_download": [{"artist": "Test Artist", "title": "Test Title"}],
        "to_download_count": 1,
    }
    monkeypatch.setattr(app_module, "_get_best_available_status", lambda: dict(broken_status))
    # Keep the route's async self-heal hook inert for a deterministic test.
    monkeypatch.setattr(app_module, "_auto_trigger_cover_backfill_if_small_gap", lambda *_a, **_k: None)

    client = app_module.app.test_client()
    resp = client.get("/api/shazam-sync/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cover_hashes"].get(TRACK_KEY) == h, (
        "/status must rebuild cover_hashes from the cover_cache dir even when the "
        "persisted map is empty — this is the permanent fix for the recurring bug"
    )


def test_status_cover_hashes_resilient_to_accented_key(app_module, cover_dir, monkeypatch):
    """File cached under folded variant; row uses the display key. /status still maps it."""
    stored = "davi, definition - desole"
    h = _write_cover(cover_dir, stored)
    broken_status = {
        "cover_hashes": {},
        "to_download": [{"artist": "Davi & Definition", "title": "Désolé"}],
        "to_download_count": 1,
    }
    monkeypatch.setattr(app_module, "_get_best_available_status", lambda: dict(broken_status))
    monkeypatch.setattr(app_module, "_auto_trigger_cover_backfill_if_small_gap", lambda *_a, **_k: None)

    client = app_module.app.test_client()
    data = client.get("/api/shazam-sync/status").get_json()
    # Primary display key resolves to the folded-variant file's hash.
    assert data["cover_hashes"].get("Davi & Definition - Désolé") == h


# =====================================================================
# Job-end backfill trigger: a scraped track without a cover file must get
# the backfill fired IMMEDIATELY when the job completes (no 10-min wait),
# so the in-page watcher fills it without a manual reload.
# =====================================================================

def test_after_job_backfill_fires_when_cover_missing(app_module, cover_dir, monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            started['target'] = target
        def start(self):
            started['started'] = True

    monkeypatch.setattr(app_module.threading, 'Thread', FakeThread)
    monkeypatch.setattr('shazam_cache.load_status_cache',
                        lambda: {'urls': {TRACK_KEY: 'https://soundeo.com/x'}, 'cover_hashes': {}})
    app_module._cover_backfill_running = False
    try:
        fired = app_module._maybe_backfill_covers_after_job('unit test')
        assert fired is True
        assert started.get('started') is True
        assert app_module._cover_backfill_progress['running'] is True
        assert app_module._cover_backfill_progress['total'] == 1
    finally:
        app_module._cover_backfill_running = False
        app_module._cover_backfill_progress = {'done': 0, 'total': 0, 'running': False}


def test_after_job_backfill_skips_when_cover_on_disk(app_module, cover_dir, monkeypatch):
    """Disk-aware: a cover file on disk counts as covered even if the persisted
    map lost the entry — no pointless backfill, no Soundeo re-fetch."""
    _write_cover(cover_dir, TRACK_KEY)
    monkeypatch.setattr('shazam_cache.load_status_cache',
                        lambda: {'urls': {TRACK_KEY: 'https://soundeo.com/x'}, 'cover_hashes': {}})
    app_module._cover_backfill_running = False
    assert app_module._maybe_backfill_covers_after_job('unit test') is False


def test_after_job_backfill_noop_when_already_running(app_module, cover_dir, monkeypatch):
    monkeypatch.setattr('shazam_cache.load_status_cache',
                        lambda: {'urls': {TRACK_KEY: 'https://soundeo.com/x'}, 'cover_hashes': {}})
    app_module._cover_backfill_running = True
    try:
        assert app_module._maybe_backfill_covers_after_job('unit test') is False
    finally:
        app_module._cover_backfill_running = False


def test_publish_cover_hash_live_rebinds_atomically(app_module, monkeypatch):
    """The in-memory publish must REBIND the dict (new object), never mutate in
    place — readers iterate it concurrently."""
    before = {'Existing - Track': 'ffff0000ffff0000ffff0000ffff0000'}
    app_module.app._shazam_sync_status = {'cover_hashes': before}
    try:
        app_module._publish_cover_hash_live(TRACK_KEY, SAMPLE_HASH)
        after = app_module.app._shazam_sync_status['cover_hashes']
        assert after is not before, "must rebind, not mutate in place"
        assert after[TRACK_KEY] == SAMPLE_HASH
        assert after[TRACK_KEY.lower()] == SAMPLE_HASH
        assert after['Existing - Track'] == 'ffff0000ffff0000ffff0000ffff0000'
        assert before == {'Existing - Track': 'ffff0000ffff0000ffff0000ffff0000'}, "old dict untouched"
    finally:
        app_module.app._shazam_sync_status = None


# =====================================================================
# Persistence hardening (defense in depth).
# =====================================================================

def test_save_status_cache_preserves_cover_hashes(monkeypatch, tmp_path):
    """A save whose status dropped cover_hashes must not shrink the persisted map."""
    import shazam_cache as sc

    status_path = str(tmp_path / "shazam_status_cache.json")
    monkeypatch.setattr(sc, "STATUS_CACHE_PATH", status_path)

    # Pre-existing file WITH cover_hashes.
    sc.save_status_cache({
        "urls": {TRACK_KEY: "https://soundeo.com/x"},
        "cover_hashes": {TRACK_KEY: SAMPLE_HASH, TRACK_KEY.lower(): SAMPLE_HASH},
        "search_outcomes": [],
    })
    # A later save that forgot cover_hashes (the bug shape).
    sc.save_status_cache({
        "urls": {TRACK_KEY: "https://soundeo.com/x"},
        "cover_hashes": {},
        "search_outcomes": [],
    })
    reloaded = sc.load_status_cache()
    assert reloaded["cover_hashes"].get(TRACK_KEY) == SAMPLE_HASH, (
        "save_status_cache must merge cover_hashes from the existing file"
    )


def test_save_json_atomic_round_trips_and_leaves_no_temp(tmp_path):
    import shazam_cache as sc
    target = str(tmp_path / "out.json")
    payload = {"a": 1, "cover_hashes": {TRACK_KEY: SAMPLE_HASH}, "unicode": "Désolé"}
    sc._save_json_atomic(target, payload)
    with open(target, encoding="utf-8") as f:
        assert json.load(f) == payload
    # No stray temp files left behind.
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_load_status_cache_restores_cover_hashes_from_bak(monkeypatch, tmp_path):
    """When the main file lost urls/outcomes, .bak restore now includes cover_hashes."""
    import shazam_cache as sc

    status_path = tmp_path / "shazam_status_cache.json"
    monkeypatch.setattr(sc, "STATUS_CACHE_PATH", str(status_path))

    # Main file: has track lists but no urls/search_outcomes (the .bak trigger).
    status_path.write_text(json.dumps({
        "to_download": [{"artist": "Test Artist", "title": "Test Title"}],
        "have_locally": [],
    }), encoding="utf-8")
    # .bak: the richer prior snapshot, including cover_hashes.
    (tmp_path / "shazam_status_cache.json.bak").write_text(json.dumps({
        "urls": {TRACK_KEY: "https://soundeo.com/x"},
        "cover_hashes": {TRACK_KEY: SAMPLE_HASH},
    }), encoding="utf-8")

    out = sc.load_status_cache()
    assert out["cover_hashes"].get(TRACK_KEY) == SAMPLE_HASH
