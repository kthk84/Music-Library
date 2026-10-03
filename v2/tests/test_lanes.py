"""Stars and downloads run in their own lanes, next to a running search."""
import threading
import time

import pytest


@pytest.fixture
def lanes(monkeypatch):
    import app as A
    _missing = object()
    saved = {k: getattr(A.app, k, _missing) for k in (
        '_shazam_sync_progress', '_shazam_star_progress', '_shazam_download_progress',
        '_shazam_single_star_queue', '_shazam_single_star_queue_lock',
        '_shazam_download_pending_queue', '_shazam_download_queue_lock', '_shazam_compare_running')}
    A.app._shazam_sync_progress = {'running': True, 'mode': 'search_global'}   # a Search all runs
    A.app._shazam_star_progress = {}
    A.app._shazam_download_progress = {}
    A.app._shazam_compare_running = False
    A.app._shazam_single_star_queue = []
    A.app._shazam_single_star_queue_lock = threading.Lock()
    A.app._shazam_download_pending_queue = []
    A.app._shazam_download_queue_lock = threading.Lock()
    yield A
    for k, v in saved.items():
        if v is _missing:
            if hasattr(A.app, k):
                delattr(A.app, k)
        else:
            setattr(A.app, k, v)


def test_star_starts_during_search_and_lane_is_exclusive(lanes, monkeypatch):
    A = lanes
    ran = []
    monkeypatch.setattr(A, '_run_star_queue_worker', lambda item: ran.append(item['key']))
    A.app._shazam_single_star_queue = [{'key': 'A - One'}, {'key': 'B - Two'}]
    assert A._kick_star_lane() is True
    assert A._kick_star_lane() is False          # lane busy with the first star
    time.sleep(0.05)
    assert ran == ['A - One']
    assert A.app._shazam_sync_progress['running']  # search progress untouched


def test_star_completion_is_reported_nested_while_search_runs(lanes):
    A = lanes
    A._star_lane_finish('A - One', 'star_single', done=1, starred=True, url='u')
    out = A.app.test_client().get('/api/shazam-sync/progress').get_json()
    assert out['mode'] == 'search_global'
    assert out['star_progress']['completed'][-1]['key'] == 'A - One'


def test_download_starts_during_search(lanes, monkeypatch):
    A = lanes
    monkeypatch.setattr(A, '_run_download_queue_worker', lambda: None)
    A.app._shazam_download_pending_queue = ['A - One', 'B - Two']
    assert A._shazam_download_start_next() is True
    assert A._shazam_download_start_next() is False   # one download at a time
    assert A.app._shazam_download_pending_queue == ['B - Two']


def test_compare_still_blocks_everything(lanes, monkeypatch):
    A = lanes
    A.app._shazam_compare_running = True
    A.app._shazam_single_star_queue = [{'key': 'A - One'}]
    A.app._shazam_download_pending_queue = ['A - One']
    assert A._kick_star_lane() is False
    assert A._shazam_download_start_next() is False
