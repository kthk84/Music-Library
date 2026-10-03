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
