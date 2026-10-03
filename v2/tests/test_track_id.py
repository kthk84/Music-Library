"""Star/unstar must toggle the track the URL points at, never a stale cached ID.

Regression: a re-search swapped a song's URL to 19723443 while track_ids still
held 19448359, so clicking ★ toggled a different track on Soundeo.
"""
URL = "https://soundeo.com/track/enrico-sangiuliano-the-techno-code-charlotte-de-witte-s-acid-code-19723443.html"
KEY = "Enrico Sangiuliano - The Techno Code (Charlotte de Witte's Acid Code)"


def test_resolve_prefers_url_id_over_stale_cache():
    import app as app_module
    status = {"track_ids": {KEY: "19448359", KEY.lower(): "19448359"}}
    assert app_module._resolve_track_id(status, KEY, URL, "unused") == "19723443"
    assert status["track_ids"][KEY] == "19723443"
    assert status["track_ids"][KEY.lower()] == "19723443"


def test_resolve_falls_back_to_cache_without_url():
    import app as app_module
    status = {"track_ids": {KEY: "19448359"}}
    assert app_module._resolve_track_id(status, KEY, "", "unused") == "19448359"


def test_set_url_replaces_stale_cached_id():
    import app as app_module
    status = {"urls": {KEY: "https://soundeo.com/track/old-19448359.html"},
              "track_ids": {KEY: "19448359", KEY.lower(): "19448359"}}
    app_module._set_url_and_track_id(status, KEY, URL, "unused")
    assert status["track_ids"][KEY] == "19723443"


def test_save_does_not_resurrect_deleted_have_locally(tmp_path, monkeypatch):
    """A have_locally entry whose file is gone must stay gone after save; re-adding
    it made every /status poll save a stale snapshot over a fresh ★."""
    import json
    import shazam_cache as sc
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(sc, "STATUS_CACHE_PATH", str(status_path))
    kept = tmp_path / "kept.aiff"
    kept.write_bytes(b"x")
    gone = {"artist": "Sam Shure", "title": "Qualified", "filepath": str(tmp_path / "deleted.aiff")}
    have = {"artist": "A", "title": "B", "filepath": str(kept)}
    status_path.write_text(json.dumps({"have_locally": [have, gone], "to_download": []}))
    sc.save_status_cache({"have_locally": [have], "to_download": [{"artist": "Sam Shure", "title": "Qualified"}]})
    saved = json.loads(status_path.read_text())
    assert [h["title"] for h in saved["have_locally"]] == ["B"]
    assert [t["title"] for t in saved["to_download"]] == ["Qualified"]


def _status_file(tmp_path, monkeypatch):
    import shazam_cache as sc
    path = tmp_path / "status.json"
    monkeypatch.setattr(sc, "STATUS_CACHE_PATH", str(path))
    return sc, path


def test_stale_snapshot_save_keeps_newer_star(tmp_path, monkeypatch):
    """Live bug: a ❤ search saved its older snapshot 2s after a ★ and flipped it back."""
    import copy, json
    sc, path = _status_file(tmp_path, monkeypatch)
    key = "Charlotte de Witte - A Prayer for the Dancefloor"
    base = {"have_locally": [], "to_download": [], "starred": {key: False, key.lower(): False}}
    sc.save_status_cache(base)
    stale = copy.deepcopy(base)              # search worker's snapshot, taken earlier
    fresh = copy.deepcopy(base)
    sc.mark_starred(fresh, key, True)        # the star lands
    sc.save_status_cache(fresh)
    sc.save_status_cache(stale)              # stale save arrives after
    saved = json.loads(path.read_text())
    assert saved["starred"][key] is True
    assert saved["starred"][key.lower()] is True


def test_newer_unstar_still_wins(tmp_path, monkeypatch):
    import copy, json
    sc, path = _status_file(tmp_path, monkeypatch)
    key = "A - B"
    s1 = {"have_locally": [], "to_download": []}
    sc.mark_starred(s1, key, True, ts=100.0)
    sc.save_status_cache(s1)
    s2 = copy.deepcopy(s1)
    sc.mark_starred(s2, key, False, ts=200.0)
    sc.save_status_cache(s2)
    assert json.loads(path.read_text())["starred"][key] is False


def test_named_remix_conflict():
    from local_scanner import _named_remix_conflict as c
    assert c("Loneliness 2010 (Roy Rosenfeld Remix)", "Loneliness (Original Mix)")
    assert c("This Is Cocaine (Regal Remix)", "This Is Cocaine (Shall Ocin Remix)")
    assert not c("X (Original Mix)", "X (Extended Mix)")
    assert not c("X (Marsh Remix)", "X (Marsh Extended Remix)")
    assert not c("NY Lipps (feat. Nancy Whang) [Kawazaki Dub]", "NY Lipps (feat. Nancy Whang) (Kawazaki Dub)")
    assert not c("Destination", "Destination (Original Mix)")
