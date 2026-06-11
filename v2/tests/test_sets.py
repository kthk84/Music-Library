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


def test_1001_dom_associates_cue_seconds_by_position(sets_file):
    """Cue inputs precede each row's MusicRecording; association is positional
    (counts don't match — mashup rows carry cues without recordings)."""
    html = (
        '<input id="tlp1_cue_seconds" type="hidden" value="0">'
        '<div itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="A - One"></div>'
        '<input id="tlp2_cue_seconds" type="hidden" value="3725">'   # 01:02:05
        '<input id="tlp3_cue_seconds" type="hidden" value="3726">'   # nearest wins
        '<div itemscope itemtype="http://schema.org/MusicRecording">'
        '<meta itemprop="name" content="B - Two"></div>'
    )
    tracks = sets_mod._tracks_from_1001_dom(html)
    assert tracks[0]["start_time"] == "00:00:00"
    assert tracks[1]["start_time"] == "01:02:06"


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
