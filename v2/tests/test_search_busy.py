"""Soundeo answers an over-eager client with EMPTY results (a real no-match returns
its default chart). Empty must mean "busy, retry", never "not found"."""
import soundeo_automation as sa

HIT = [{"track_id": "1", "title": "Patrick Topping - Mi Casa (Original Mix)",
        "href": "https://soundeo.com/track/patrick-topping-mi-casa-original-mix-1.html", "favored": False}]
CHART = [{"track_id": "9", "title": "CASSIMM - Your Lovin' (Extended Mix)",
          "href": "https://soundeo.com/track/x-9.html", "favored": False}]


def _patch(monkeypatch, tmp_path, answers):
    cookies = tmp_path / "c.json"
    cookies.write_text("[]")
    calls = {"n": 0}

    def fake_search(q, cp):
        calls["n"] += 1
        return answers(calls["n"])
    monkeypatch.setattr(sa, "soundeo_api_search", fake_search)
    monkeypatch.setattr(sa, "soundeo_api_get_favorite_state", lambda u, c: False)
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)
    sa._SEARCH_BACKOFF["until"] = 0.0
    return str(cookies), calls


def test_empty_then_results_is_found(tmp_path, monkeypatch):
    cp, _ = _patch(monkeypatch, tmp_path, lambda n: [] if n == 1 else HIT)
    r = sa.run_search_tracks_http([{"artist": "Patrick Topping", "title": "Mi Casa"}], cp)
    assert r["urls"]["Patrick Topping - Mi Casa"].endswith("-1.html")
    assert r["busy_keys"] == []


def test_always_empty_is_busy_not_not_found(tmp_path, monkeypatch):
    cp, _ = _patch(monkeypatch, tmp_path, lambda n: [])
    msgs = []
    r = sa.run_search_tracks_http([{"artist": "Patrick Topping", "title": "Mi Casa"}], cp,
                                  on_progress=lambda *a, **k: msgs.append(a[2]))
    assert r["busy_keys"] == ["Patrick Topping - Mi Casa"]
    assert r["failed"] == 0
    assert not any("not found" in m.lower() for m in msgs)


def test_chart_answer_is_a_real_not_found(tmp_path, monkeypatch):
    cp, _ = _patch(monkeypatch, tmp_path, lambda n: CHART)
    r = sa.run_search_tracks_http([{"artist": "Patrick Topping", "title": "Mi Casa"}], cp)
    assert r["failed"] == 1 and r["busy_keys"] == []


def test_parallel_workers_find_all(tmp_path, monkeypatch):
    cp, _ = _patch(monkeypatch, tmp_path, lambda n: HIT)
    tracks = [{"artist": "Patrick Topping", "title": "Mi Casa"}] * 1 + [
        {"artist": "Patrick Topping", "title": f"Mi Casa {i}"} for i in range(5)]
    r = sa.run_search_tracks_http(tracks, cp, workers=3)
    assert r["done"] + r["failed"] == 6
