# SoundBridge v2

Refactor / hardening fork of SoundBridge. **Shares v1's state** (cover_cache, browser profile, status cache) so there's no data divergence — switching between v1 and v2 is just stopping one process and starting the other.

> **Hard rule:** never run v1 and v2 simultaneously. They write to the same `shazam_status_cache.json`. Concurrent writers = corrupt state.

---

## Quick start

```bash
cd ~/Code/Antigravity\ Projects/KeithKornson\ BV/SoundBridge\ v2
./run_v2.sh           # browser mode on http://127.0.0.1:5003
./run_v2.sh desktop   # native window via pywebview on the same port
```

Stop with Ctrl+C. To go back to v1: stop v2, then `cd ../SoundBridge && python3 app.py`.

---

## What's different from v1 (as of 2026-04-29)

All changes are **safe-additive** — same observable behavior, more visibility, fewer ways state can silently corrupt. No god-file split yet (that's the next session's work).

| Area | Change | Where |
|---|---|---|
| **Thumbnail self-heal** | `cover_hashes` rebuilds itself from disk when sparse vs `urls`. Eliminates the recurring "thumbnails go blank after refresh" bug class. (Already in v1 from session 3.) | [`app.py:_rebuild_cover_hashes_from_disk`](app.py), call site in `_merge_preserved_urls_into_status` |
| **Status-cache schema** | TypedDict + non-fatal `validate_status()` runs on every save. Logs a single WARNING with all issues (missing required keys, wrong types). Never raises — write proceeds regardless. | [`models/status_schema.py`](models/status_schema.py); wired in `shazam_cache.save_status_cache` |
| **Silent except sweep** | All 39 `except: pass` blocks in `app.py` replaced with `logging.debug("silent except at app.py:N", exc_info=True)`. Same behavior; visible when DEBUG logging is enabled. Searchable via `grep "silent except" flask*.log`. | `app.py` (39 sites); backup at `app.py.silent_except_sweep.bak` |
| **Shared-state runtime** | `app_paths.get_project_root_for_data` honors `SOUNDBRIDGE_DATA_DIR` env var. v2's `run_v2.sh` sets it to v1's folder. | [`app_paths.py:get_project_root_for_data`](app_paths.py) |
| **Configurable port** | `SOUNDBRIDGE_PORT` env var (default 5003 for v2 vs 5002 for v1). | `app.py` and `launch_desktop.py` |

## What's NOT changed yet

The big architectural moves from the [CTO audit](../MP3%20Cleaner/docs/kb/audience/SOUNDBRIDGE_CTO_AUDIT.md) are queued for dedicated sessions:

- **Split `app.py` (6,170 lines, 67 routes) into `routes/` + `jobs/` blueprints.** 2–3 days. Biggest win.
- **Unified `Queue` abstraction** for the 4 ad-hoc background workers (download / search / star / unstar).
- **Frontend module split** of `static/app.js` (5,881 lines).
- **CI smoke test** in GitHub Actions.
- **Silent-except sweep on `soundeo_automation.py`, `shazam_cache.py`, `local_scanner.py`, `config_shazam.py`** (only `app.py` swept this session).

These intentionally land one-per-session in v2 with verification before each commit. v1 stays as the working fallback throughout.

## How fallback works

If v2 has a bug, kill it (`Ctrl+C`) and start v1:

```bash
cd ../SoundBridge
python3 app.py
```

v1 reads the **same** `shazam_status_cache.json` v2 was just writing, so all your stars / found URLs / dot state are intact. The only thing v1 won't have is whatever code-path-specific state was in memory when v2 crashed (e.g. an in-flight download queue) — that gets lost regardless of which version is running.

## State files (live in v1's folder, shared with v2)

| File | What it holds |
|---|---|
| `shazam_status_cache.json` | The single source of truth. Dot state, urls, starred, search outcomes, cover hashes, queues. |
| `shazam_status_cache.json.bak` | Auto-shadow on every write. |
| `shazam_cache.json` | The Shazam library snapshot (last fetch). |
| `local_scan_cache.json` | Most recent local-folder scan result. |
| `shazam_skip_list.json` | Tracks user has dismissed from "to download". |
| `soundeo_cookies.json` | Soundeo session cookies (lives in `MP3 Cleaner/` historically; both versions read it). |
| `cover_cache/` | Downloaded cover art images, named `<md5(key)>.jpg`. |
| `.soundeo_browser_profile/` | Chrome user-data-dir for Selenium-based Soundeo automation. |

## Verifying v2 works end-to-end

1. Stop v1 if running.
2. `./run_v2.sh`
3. Open http://127.0.0.1:5003
4. **Thumbnails should appear** (the recovery script also persists this; if you restarted Flask the in-app self-heal does the same).
5. **Smoke check:** click on the Sync tab, confirm starred / found tracks show with correct dots.
6. **Schema check:** in Flask logs, you may see `[save_status_cache] status schema check: N issue(s): ...` if any code path writes a status missing fields. That's the schema validator working — entries get logged but the file still gets written. Report any persistent warnings; they reveal which code path needs a fix.

## Logs

Same paths as v1 (since the data dir is shared): `flask.log` etc. in v1's folder. To see DEBUG-level output (silent-except traces, schema check details), launch with:

```bash
PYTHONUNBUFFERED=1 LOGLEVEL=DEBUG ./run_v2.sh
```

(Note: setting `LOGLEVEL` requires the app to honor it — currently logging is configured implicitly by Flask's debug mode. Adjusting log level is a follow-up if you need it.)
