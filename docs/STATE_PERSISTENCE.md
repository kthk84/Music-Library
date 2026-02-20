# State persistence (app-wide)

**For developers and AI agents:** All states, events, and status of the app must be saved after each interaction and remembered after the next load or refresh. Nothing user-visible should be lost on reload or restart. This applies to **everything that happens**: fetched links, favorited/starred state, compare results, skip list, settings, etc.

## Rule

- **After every interaction** (scan folder, fetch link, favorite, compare, skip, save session, change settings, etc.), **save the resulting state**.
- **On every load or refresh (and after restarting the app)**, **restore** all saved state so the UI shows the same data and status as before.
- Do not rely only on in-memory state. Assume the user may refresh or close and reopen the app at any time.
- **Fetched links** and **starred-in-Soundeo** state must persist: when a link is fetched or a track is favorited/starred, that must still be visible after refresh or restart.

## What we persist

| State | Where stored | Restored when |
|-------|--------------|----------------|
| **App state** (last folder path, last scan count) | `app_state.json` + `localStorage` key `mp3cleaner_app_state` | Page load: `restoreAppState()` + GET `/api/app-state` |
| Compare result (to_download, have_locally, counts) | `shazam_status_cache.json` | Page load, `/api/shazam-sync/status`, bootstrap |
| Favorited / fetched track URLs (Soundeo links per track) | `shazam_status_cache.json` → `urls` | Same as above; merged when sync completes |
| **Starred in Soundeo** (per-track: is this track starred?) | `shazam_status_cache.json` → `starred` | Same as above; merged when sync completes |
| **Dismissed tracks** (tracks user dismissed — unstarred on Soundeo; "Ignored" filter) | `shazam_status_cache.json` → `dismissed` | Status, bootstrap; POST `/api/shazam-sync/dismiss-track`, `/api/shazam-sync/clear-dismissed`, `/api/shazam-sync/undismiss-track`. Always loaded from cache when in-memory is empty so ignore state is never lost. |
| **Dismissed Manual check** (track keys user dismissed) | `shazam_status_cache.json` → `dismissed_manual_check` | Status, bootstrap; POST `/api/shazam-sync/dismiss-manual-check` |
| **Soundeo track title** (exact filename/details as on Soundeo per track) | `shazam_status_cache.json` → `soundeo_titles` | Set when sync completes; merged when rebuilding status |
| **Soundeo track ID** (numeric ID per track for HTTP star/unstar) | `shazam_status_cache.json` → `track_ids` | Set when resolved from URL or by visiting track page; merged when rebuilding/merging status. Backfill: `python3 scripts/backfill_track_ids.py`. Single source of truth: same file only. |
| **Search/favorite debug log** (what starred state we see on Soundeo when you click Search) | `search_favorite.log` (project root) | Written on each per-row Search and each batch Search when a track is found; logs `get_track_starred_state` (single) or HTTP favorite state (batch) and whether `status.starred` was updated. Use to see why the blue star on Soundeo might not show as favorite in the app. |
| **Searched but not found** (per-track: Search ran and no link found) | `shazam_status_cache.json` → `not_found` | Set when Search all or per-row Search runs and finds no URL; cleared when a URL is later set. Used for row dot: grey = not yet searched, light orange = searched not found. **UI must update live:** when per-row Search completes with “not found”, the dot must turn from grey to orange without a page refresh (frontend updates `shazamNotFound` and re-renders; do not replace `shazamNotFound` inside `shazamRenderTrackList` or the update is wiped). |
| **Starred from Soundeo** (source of truth) | Crawl `https://soundeo.com/account/favorites` → merge into `starred`, `urls`, `soundeo_titles` | At start of Run Soundeo sync; also POST `/api/shazam-sync/sync-favorites-from-soundeo` (Sync favorites button) |
| Skip list (tracks user chose to skip) | `shazam_skip_list.json` | Compare, status, skip API |
| Shazam track list | `shazam_cache.json` | Fetch, compare, status |
| Local folder scan | `local_scan_cache.json` | Compare, rescan |
| Soundeo session (cookies) | `soundeo_cookies.json` (path in config) | Sync run, Save Session flow |
| **Soundeo download folder** (one of destination folders for AIFF saves) | `config.json` → `soundeo_download_folder` | Bootstrap, settings API; UI: one toggle active per folder |
| **Download last run** (done/failed/results, no_credits) | `shazam_status_cache.json` → `download_last_run` | Set after each download queue run; status API |
| Settings (folders, headed mode, etc.) | `config.json` | Bootstrap, settings API |
| **Shazam list filter** (All / Have / To DL / Skipped / Ignored) | `localStorage` → `mp3cleaner_shazam_filter_status` | Page load; filter button click |

## Implementation notes

- **App state** (`shazam_cache.load_app_state` / `save_app_state`): Persists `last_folder_path`, `last_scan_count`. Saved after every successful scan. Frontend also keeps `last_folder_path` in `localStorage` and restores it on DOMContentLoaded; GET `/api/app-state` provides server-side state. Reset (Start over) clears the folder from app state and storage.
- **Status cache** (`shazam_cache.save_status_cache` / `load_status_cache`): Holds `to_download`, `have_locally`, `folder_stats`, **`urls`** (map of `"Artist - Title"` → Soundeo track URL), **`track_ids`** (map of `"Artist - Title"` → Soundeo numeric track ID for HTTP API), **`soundeo_titles`** (map of `"Artist - Title"` → exact track title/filename as shown on Soundeo), **`starred`** (map of `"Artist - Title"` → true if starred in Soundeo), **`not_found`** (map of track keys that were searched and no Soundeo link found), **`dismissed`** (map of `"Artist - Title"` → true if user dismissed/unstarred on Soundeo), and **`dismissed_manual_check`** (list of track keys for which the user dismissed the Manual check message). When sync completes, merge new `urls`, `soundeo_titles`, and `starred` into current status and call `save_status_cache`. When rebuilding status from caches, copy `urls`, `track_ids`, `soundeo_titles`, `starred`, `dismissed`, and `dismissed_manual_check` from the previous status cache so they survive refresh and restart.
- **Single source of truth (one file only):** The only file for status and dot state is **`shazam_status_cache.json`**. It holds `to_download`, `have_locally`, `urls`, `track_ids`, `not_found`, `starred`, `soundeo_titles`, `dismissed`, and **`search_outcomes`** (the per-track search history: list of `{t, a, k, u}`). No separate log file or other storage. When returning status, `urls` and `not_found` are derived from `search_outcomes` (replay, newest per key wins) so the UI always matches what happened. When rebuilding or merging preserved state, `not_found` is **replaced** from the file, never merged from elsewhere.
- **Dot state: one-time wipe, then persist every change.** A one-time wipe was done so all no-link tracks show grey. From that point on, every action that moves a dot (grey → orange, grey → link, orange → link) is saved immediately to the status cache. We never need to wipe again. State is always backtrackable: refresh or restart restores the last saved state from `shazam_status_cache.json`. To clear the not-found paper trail (all orange → grey), run `python3 scripts/reset_not_found.py`; there is no UI button for this.
- **Search outcomes in the same file:** Per-track search results are in **`search_outcomes`** inside the status cache (max 100k entries). Dot state is derived from this when we read; no separate log. **Batch search saves as soon as each track result is known** (found/not found), so restart mid-batch doesn’t lose progress and we never write a partial “all not_found” state. Rebuild (script or POST `/api/shazam-sync/rebuild-from-search-log`) replays `search_outcomes` if ever needed.
- **Frontend**: On load, `restoreAppState()` runs first (folder path from localStorage + `/api/app-state`). Bootstrap (`/api/shazam-sync/bootstrap`) returns full status including `urls` and `starred`; `shazamApplyStatus(status)` sets `shazamTrackUrls` and `shazamStarred` so links and starred state show after refresh.
- Any new user-visible state (e.g. “marked as done”, custom labels) should be saved to an appropriate cache and included in the status or settings returned to the client.

## For AI agents

When adding or changing any feature in this app:

1. **Persist** every state change that results from an interaction (scan folder, fetched link, favorited, starred, skipped, compare result, settings change, etc.). Save after each such action. Nothing that the user sees or does should be lost on refresh or restart.
2. **Restore** that state when the page loads or when the API that serves the UI is called (bootstrap, status, app-state, etc.). Bootstrap and status must return full status including `urls` and `starred` so the frontend can restore links and starred state.
3. **Merge**, don’t overwrite, when combining new data with existing (e.g. new favorited URLs and starred keys merged into existing `urls` and `starred`).
4. Keep this doc and `AGENTS.md` in sync with any new persisted state.