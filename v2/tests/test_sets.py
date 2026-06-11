"""Tests for the tracklist-sets feature (lib/sets.py + /api/sets endpoints).

The trackid.net fixture is a trimmed REAL API payload from
https://trackid.net/api/public/audiostreams/inner-rhythms-episode-29-… so the
parser is pinned against the actual schema, not a guess.
"""
import json
import os

import pytest

import lib.sets as sets_mod

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class _FakeResp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def sets_file(tmp_path, monkeypatch):
    p = tmp_path / "sets.json"
    monkeypatch.setattr(sets_mod, "SETS_PATH", str(p))
    return p


# ------------------------------------------------------------- trackid.net ---

def test_scrape_trackid_merges_processes_and_sorts(monkeypatch, sets_file):
    payload = json.load(open(os.path.join(FIXTURE_DIR, "trackid_audiostream.json")))

    def fake_get(url, timeout=20):
        assert "/api/public/audiostreams/" in url
        return _FakeResp(200, payload=payload)

    monkeypatch.setattr(sets_mod, "_http_get", fake_get)
    out = sets_mod.scrape_set_from_url(
        "https://trackid.net/audiostreams/inner-rhythms-episode-29-live-at-inner-rhythms-the-other-side"
    )
    assert out["source"] == "trackid.net"
    assert "Inner Rhythms" in out["title"]
    tracks = out["tracks"]
    assert len(tracks) >= 4, "tracks from BOTH detection processes must be merged"
    # Sorted by start time ascending
    times = [t["start_time"] for t in tracks if t["start_time"]]
    assert times == sorted(times)
    # Real first track from the fixture
    assert tracks[0]["artist"].startswith("Shingo Nakamura")
    assert tracks[0]["title"] == "Worlds Apart (PROFF Remix)"
    assert out["id"] == sets_mod.set_id_for_url(out["url"])


def test_scrape_trackid_empty_raises(monkeypatch, sets_file):
    monkeypatch.setattr(sets_mod, "_http_get",
                        lambda url, timeout=20: _FakeResp(200, payload={"result": {"title": "X", "detectionProcesses": []}}))
    with pytest.raises(ValueError):
        sets_mod.scrape_set_from_url("https://trackid.net/audiostreams/some-empty-stream")


# ----------------------------------------------------------------- JSON-LD ---

_LD_HTML = """<html><head><title>Huminal - Inner Rhythms 029 | 1001Tracklists</title>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"MusicPlaylist","name":"Inner Rhythms 029",
 "track":{"@type":"ItemList","itemListElement":[
   {"@type":"ListItem","position":1,"item":{"@type":"MusicRecording","name":"In A Distorted Galaxy","byArtist":{"name":"Sébastien Léger"}}},
   {"@type":"ListItem","position":2,"item":{"@type":"MusicRecording","name":"Calling (Extended Mix)","byArtist":{"name":"Trilucid"}}}
 ]}}
</script></head><body></body></html>"""


def test_scrape_1001_via_jsonld(monkeypatch, sets_file):
    monkeypatch.setattr(sets_mod, "_http_get", lambda url, timeout=20: _FakeResp(200, text=_LD_HTML))
    out = sets_mod.scrape_set_from_url("https://www.1001tracklists.com/tracklist/abc/test.html")
    assert out["source"] == "1001tracklists"
    assert out["title"].startswith("Huminal - Inner Rhythms 029")
    assert [t["artist"] for t in out["tracks"]] == ["Sébastien Léger", "Trilucid"]


def test_scrape_1001_cloudflare_falls_back_to_browser(monkeypatch, sets_file):
    monkeypatch.setattr(sets_mod, "_http_get",
                        lambda url, timeout=20: _FakeResp(503, text="cf-browser-verification Just a moment"))
    fetched = {}

    def fake_browser(url):
        fetched["url"] = url
        return _LD_HTML

    out = sets_mod.scrape_set_from_url("https://www.1001tracklists.com/tracklist/abc/test.html",
                                       fetch_html=fake_browser)
    assert fetched["url"].startswith("https://www.1001tracklists.com")
    assert len(out["tracks"]) == 2


def test_scrape_1001_blocked_without_browser_raises(monkeypatch, sets_file):
    monkeypatch.setattr(sets_mod, "_http_get",
                        lambda url, timeout=20: _FakeResp(503, text="challenge-platform"))
    with pytest.raises(ValueError) as e:
        sets_mod.scrape_set_from_url("https://www.1001tracklists.com/tracklist/abc/test.html")
    assert "Cloudflare" in str(e.value)


def test_1001_dom_fallback_parses_trackvalues(sets_file):
    html = '<div><span class="trackValue">Artist One - Title One</span>' \
           '<span class="trackValue">Artist Two - Title Two (Remix)</span></div>'
    tracks = sets_mod._tracks_from_1001_dom(html)
    assert [t["title"] for t in tracks] == ["Title One", "Title Two (Remix)"]


def test_1001_dom_parses_real_musicrecording_metas(sets_file):
    """Real 1001tracklists row markup (captured from a live headed-browser
    fetch): MusicRecording itemscope with name/byArtist metas. Page-level
    name metas (outside MusicRecording) must NOT leak in as tracks."""
    html = (
        '<meta itemprop="name" content="Some Page Title | 1001Tracklists">'
        '<div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="Shingo Nakamura &amp; Warung - Worlds Apart (PROFF Remix)">'
        '<meta itemprop="byArtist" content="Shingo Nakamura &amp; Warung">'
        '<span class="trackValue notranslate blueTxt">…</span></div>'
        '<div itemprop="tracks" itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="ID - ID"></div>'
    )
    tracks = sets_mod._tracks_from_1001_dom(html)
    assert len(tracks) == 2, "exactly the two MusicRecording rows, no page-title junk"
    assert tracks[0]["artist"] == "Shingo Nakamura & Warung"
    assert tracks[0]["title"] == "Worlds Apart (PROFF Remix)"
    assert tracks[1] == {"artist": "ID", "title": "ID", "start_time": ""}


def test_1001_dom_row_anchored_includes_id_rows(sets_file):
    """Rows are anchored on cue inputs. Known tracks parse from MusicRecording
    metas; unknown (ID) rows have NO schema markup — only trackValue text — and
    must STILL be listed with their timestamp so they're listenable via the set
    player (a real 73-row set used to parse as 49)."""
    html = (
        '<input id="tlp1_cue_seconds" type="hidden" value="0">'
        '<div itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="A - One"></div>'
        '<input id="tlp2_cue_seconds" type="hidden" value="1320">'
        '<span class="trackValue notranslate">ID</span>'             # unknown track row
        '<input id="tlp3_cue_seconds" type="hidden" value="3726">'
        '<div itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="B - Two"></div>'
    )
    tracks = sets_mod._tracks_from_1001_dom(html)
    assert len(tracks) == 3
    assert tracks[0] == {"artist": "A", "title": "One", "start_time": "00:00:00"}
    assert tracks[1] == {"artist": "ID", "title": "ID", "start_time": "00:22:00"}
    assert tracks[2]["start_time"] == "01:02:06"


# ----------------------------------------------------------------- generic ---

def test_scrape_generic_line_regex(monkeypatch, sets_file):
    html = "<html><body><div>01. Some Artist - Some Title</div><div>02. Other Guy - Other Tune</div></body></html>"
    monkeypatch.setattr(sets_mod, "_http_get", lambda url, timeout=20: _FakeResp(200, text=html))
    out = sets_mod.scrape_set_from_url("https://example.com/sets/whatever")
    assert len(out["tracks"]) == 2
    assert out["tracks"][0]["artist"] == "Some Artist"


def test_scrape_rejects_non_url(sets_file):
    with pytest.raises(ValueError):
        sets_mod.scrape_set_from_url("not a url")


# -------------------------------------------------------------- stream url ---

def test_trackid_carries_stream_url(monkeypatch, sets_file):
    payload = json.load(open(os.path.join(FIXTURE_DIR, "trackid_audiostream.json")))
    monkeypatch.setattr(sets_mod, "_http_get", lambda url, timeout=20: _FakeResp(200, payload=payload))
    out = sets_mod.scrape_set_from_url("https://trackid.net/audiostreams/whatever-slug")
    assert out["stream_url"].startswith("https://soundcloud.com/"), \
        "trackid sets must carry the source stream for in-app playback"


def test_extract_stream_url_prefers_sc_track_id():
    html = ('<a href="https://soundcloud.com/huminalmusic">profile</a>'
            '<div data-x="soundcloud.com/tracks/2314934267"></div>'
            '<a href="https://youtube.com/watch?v=abc123XYZ_-">yt</a>')
    assert sets_mod._extract_stream_url(html) == "https://api.soundcloud.com/tracks/2314934267"


def test_extract_stream_url_youtube_and_permalink_fallbacks():
    assert sets_mod._extract_stream_url('x youtu.be/dQw4w9WgXcQ x') == \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    html = '<a href="https://soundcloud.com/someartist/some-mix-029">listen</a>'
    assert sets_mod._extract_stream_url(html) == "https://soundcloud.com/someartist/some-mix-029"
    # 1001tracklists' own profile must not be mistaken for the stream
    assert sets_mod._extract_stream_url('href="https://soundcloud.com/1001tracklists/likes"') == ""


# ----------------------------------------------------- storage + endpoints ---

def test_sets_storage_round_trip_and_replace(sets_file):
    s1 = {"id": "abc", "url": "https://x/1", "title": "One", "tracks": [], "track_count": 0, "created_at": 1}
    sets_mod.add_or_replace_set(s1)
    assert sets_mod.load_sets()[0]["title"] == "One"
    # same id replaces, no duplicate
    sets_mod.add_or_replace_set({**s1, "title": "One v2"})
    loaded = sets_mod.load_sets()
    assert len(loaded) == 1 and loaded[0]["title"] == "One v2"
    sets_mod.delete_set("abc")
    assert sets_mod.load_sets() == []


# ------------------------------------------------------------- like flow ---

@pytest.fixture
def like_env(monkeypatch, tmp_path):
    """Isolated caches + recorded side effects for the ❤ pipeline."""
    import app as app_module
    import shazam_cache as sc

    state = {"status": {}, "shazam": [], "search_q": [], "stars": [], "search_started": 0}
    monkeypatch.setattr(sc, "load_status_cache", lambda: dict(state["status"]))
    monkeypatch.setattr(sc, "save_status_cache", lambda s: state.__setitem__("status", dict(s)))
    monkeypatch.setattr(sc, "load_shazam_cache", lambda: list(state["shazam"]))
    monkeypatch.setattr(sc, "save_shazam_cache", lambda t: state.__setitem__("shazam", list(t)))
    monkeypatch.setattr(app_module, "_shazam_any_job_running", lambda: True)  # don't spawn threads
    monkeypatch.setattr(app_module, "_enqueue_single_star",
                        lambda key, artist, title, url, start=True: state["stars"].append((key, url)))
    app_module.app._shazam_sync_status = None
    app_module.app._shazam_single_search_queue = state["search_q"]
    import threading as _t
    app_module.app._shazam_single_search_queue_lock = _t.Lock()
    yield app_module, state
    app_module.app._shazam_sync_status = None


def test_like_adds_to_library_flags_and_queues_search(like_env):
    app_module, state = like_env
    client = app_module.app.test_client()
    r = client.post("/api/sets/like", json={"artist": "Mayro & Tali Muss", "title": "Fantom", "liked": True})
    assert r.status_code == 200 and r.get_json()["queued"] == "search"
    st = state["status"]
    key = "Mayro & Tali Muss - Fantom"
    # joins the main Sync list (manual library entry + to_download row)
    assert any(t["artist"] == "Mayro & Tali Muss" for t in state["shazam"])
    assert state["shazam"][0].get("origin") == "set"
    assert any(t["artist"] == "Mayro & Tali Muss" for t in st["to_download"])
    # like markers
    assert st["maybe"].get(key) is True
    assert st["auto_star_on_found"].get(key) is True
    # search queued (worker spawn suppressed by busy-flag)
    assert state["search_q"] == [{"artist": "Mayro & Tali Muss", "title": "Fantom"}]
    # idempotent: re-like doesn't duplicate
    client.post("/api/sets/like", json={"artist": "Mayro & Tali Muss", "title": "Fantom", "liked": True})
    assert sum(1 for t in state["status"]["to_download"] if t["artist"] == "Mayro & Tali Muss") == 1


def test_like_with_known_url_converts_straight_to_star(like_env):
    app_module, state = like_env
    state["status"] = {"urls": {"A - B": "https://soundeo.com/track/x"}}
    client = app_module.app.test_client()
    r = client.post("/api/sets/like", json={"artist": "A", "title": "B", "liked": True})
    assert r.get_json()["queued"] == "star"
    assert state["stars"] == [("A - B", "https://soundeo.com/track/x")]
    # like marker consumed by the conversion; maybe cleared (graduated)
    assert not state["status"].get("auto_star_on_found", {}).get("A - B")
    assert not state["status"].get("maybe", {}).get("A - B")


def test_unlike_clears_markers(like_env):
    app_module, state = like_env
    client = app_module.app.test_client()
    client.post("/api/sets/like", json={"artist": "A", "title": "B", "liked": True})
    client.post("/api/sets/like", json={"artist": "A", "title": "B", "liked": False})
    st = state["status"]
    assert not st.get("maybe", {}).get("A - B")
    assert not st.get("auto_star_on_found", {}).get("A - B")


def test_convert_like_to_star_if_pending(like_env):
    app_module, state = like_env
    status = {"auto_star_on_found": {"A - B": True, "a - b": True}, "maybe": {"A - B": True}}
    fired = app_module._convert_like_to_star_if_pending(status, "A - B", "A", "B", "https://soundeo.com/t", already_starred=False)
    assert fired is True
    assert state["stars"] == [("A - B", "https://soundeo.com/t")]
    assert status["auto_star_on_found"] == {}
    assert not status["maybe"].get("A - B")
    # no marker → no-op
    assert app_module._convert_like_to_star_if_pending(status, "A - B", "A", "B", "u", False) is False
    # already starred → marker cleared, no star queued
    status2 = {"auto_star_on_found": {"C - D": True}}
    assert app_module._convert_like_to_star_if_pending(status2, "C - D", "C", "D", "u", already_starred=True) is True
    assert len(state["stars"]) == 1


# --------------------------------------------------- API (no-browser) search ---

def test_search_mode_settings_roundtrip(monkeypatch, tmp_path):
    import app as app_module
    import config_shazam as cfg
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "get_config_path", lambda: str(cfg_path))
    # load/save in config_shazam read via get_config_path? Ensure isolation by
    # patching load/save directly.
    state = {"cfg": {}}
    monkeypatch.setattr(cfg, "load_config", lambda: dict(state["cfg"]))
    monkeypatch.setattr(cfg, "save_config", lambda c: state.__setitem__("cfg", dict(c)))
    client = app_module.app.test_client()
    r = client.post("/api/settings", json={"search_mode": "browser_visible"})
    assert r.status_code == 200
    assert state["cfg"].get("search_mode") == "browser_visible"
    # invalid value ignored
    client.post("/api/settings", json={"search_mode": "yolo"})
    assert state["cfg"].get("search_mode") == "browser_visible"
    monkeypatch.setattr(cfg, "load_config", lambda: {"search_mode": "browser_visible"})
    assert app_module._get_search_mode() == "browser_visible"
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    assert app_module._get_search_mode() == "api", "API must be the default"


def test_apply_single_search_result_found_and_notfound(monkeypatch):
    import app as app_module
    import shazam_cache as sc

    state = {"status": {}}
    monkeypatch.setattr(sc, "load_status_cache", lambda: dict(state["status"]))
    monkeypatch.setattr(sc, "save_status_cache", lambda s: state.__setitem__("status", dict(s)))
    monkeypatch.setattr(app_module, "_set_url_and_track_id",
                        lambda status, key, url, cookies_path=None: status["urls"].__setitem__(key, url))
    monkeypatch.setattr(app_module, "_cache_cover_art", lambda key, url: None)
    app_module.app._shazam_sync_status = None

    app_module._apply_single_search_result("A", "B", True, url="https://soundeo.com/t1",
                                           soundeo_title="A - B (Extended)", score=0.97, starred=False)
    st = state["status"]
    assert st["urls"]["A - B"] == "https://soundeo.com/t1"
    assert st["soundeo_titles"]["A - B"] == "A - B (Extended)"
    assert st["starred"]["A - B"] is False
    assert len(st["search_outcomes"]) == 1

    # Second result (sequential here; in prod the apply lock serializes parallel
    # workers): outcomes must ACCUMULATE — nothing lost.
    app_module._apply_single_search_result("C", "D", False)
    st = state["status"]
    assert len(st["search_outcomes"]) == 2
    assert st["not_found"]["C - D"] is True
    assert st["urls"]["A - B"] == "https://soundeo.com/t1", "earlier result must survive"


def test_apply_single_search_result_fires_like_conversion(monkeypatch):
    import app as app_module
    import shazam_cache as sc

    state = {"status": {"auto_star_on_found": {"A - B": True}, "maybe": {"A - B": True}}, "stars": []}
    monkeypatch.setattr(sc, "load_status_cache", lambda: dict(state["status"]))
    monkeypatch.setattr(sc, "save_status_cache", lambda s: state.__setitem__("status", dict(s)))
    monkeypatch.setattr(app_module, "_set_url_and_track_id",
                        lambda status, key, url, cookies_path=None: status["urls"].__setitem__(key, url))
    monkeypatch.setattr(app_module, "_enqueue_single_star",
                        lambda key, artist, title, url, start=True: state["stars"].append((key, start)))
    app_module.app._shazam_sync_status = None

    app_module._apply_single_search_result("A", "B", True, url="https://soundeo.com/t1")
    assert state["stars"] == [("A - B", False)], "conversion enqueues star WITHOUT starting it"
    assert not state["status"]["maybe"].get("A - B")


def test_start_next_single_search_dispatches_by_mode(monkeypatch):
    import app as app_module
    import threading as _t

    spawned = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            spawned.append(getattr(target, "__name__", str(target)))
        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    app_module.app._shazam_single_search_queue = [{"artist": "A", "title": "B"}, {"artist": "C", "title": "D"}]
    app_module.app._shazam_single_search_queue_lock = _t.Lock()

    monkeypatch.setattr(app_module, "_get_search_mode", lambda: "api")
    app_module._search_http_active = 0
    app_module._start_next_single_search()
    assert spawned == ["_run_search_single_http_drainer", "_run_search_single_http_drainer"], \
        "api mode spawns parallel drainers (capped at queue length)"
    app_module._search_http_active = 0

    spawned.clear()
    monkeypatch.setattr(app_module, "_get_search_mode", lambda: "browser_hidden")
    app_module._start_next_single_search()
    assert spawned == ["_run_search_soundeo_single"], "browser mode stays sequential"
    app_module.app._shazam_single_search_queue = []


def test_sets_api_endpoints(monkeypatch, sets_file):
    import app as app_module

    monkeypatch.setattr(sets_mod, "scrape_set_from_url",
                        lambda url, fetch_html=None: {
                            "id": sets_mod.set_id_for_url(url), "url": url, "source": "test",
                            "title": "T", "created_at": 1, "track_count": 1,
                            "tracks": [{"artist": "A", "title": "B", "start_time": ""}],
                        })
    client = app_module.app.test_client()
    r = client.post("/api/sets/add", json={"url": "https://trackid.net/audiostreams/zzz"})
    assert r.status_code == 200, r.data
    sets = r.get_json()["sets"]
    assert sets[0]["title"] == "T"

    r2 = client.get("/api/sets")
    assert r2.get_json()["sets"][0]["tracks"][0]["artist"] == "A"

    r3 = client.post("/api/sets/delete", json={"id": sets[0]["id"]})
    assert r3.status_code == 200
    assert r3.get_json()["sets"] == []

    r4 = client.post("/api/sets/add", json={})
    assert r4.status_code == 400
