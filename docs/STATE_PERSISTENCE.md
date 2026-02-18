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
| **Dismissed tracks** (tracks user dismissed — unstarred on Soundeo) | `shazam_status_cache.json` → `dismissed` | Status, bootstrap; POST `/api/shazam-sync/dismiss-track`, `/api/shazam-sync/undismiss-track` |
| **Dismissed Manual check** (track keys user dismissed) | `shazam_status_cache.json` → `dismissed_manual_check` | Status, bootstrap; POST `/api/shazam-sync/dismiss-manual-check` |
| **Soundeo track title** (exact filename/details as on Soundeo per track) | `shazam_status_cache.json` → `soundeo_titles` | Set when sync completes; merged when rebuilding status |
| **Starred from Soundeo** (source of truth) | Crawl `https://soundeo.com/account/favorites` → merge into `starred`, `urls`, `soundeo_titles` | At start of Run Soundeo sync; also POST `/api/shazam-sync/sync-favorites-from-soundeo` (Sync favorites button) |
| Skip list (tracks user chose to skip) | `shazam_skip_list.json` | Compare, status, skip API |
| Shazam track list | `shazam_cache.json` | Fetch, compare, status |
| Local folder scan | `local_scan_cache.json` | Compare, rescan |
| Soundeo session (cookies) | `soundeo_cookies.json` (path in config) | Sync run, Save Session flow |
| Settings (folders, headed mode, etc.) | `config.json` | Bootstrap, settings API |

## Implementation notes

- **App state** (`shazam_cache.load_app_state` / `save_app_state`): Persists `last_folder_path`, `last_scan_count`. Saved after every successful scan. Frontend also keeps `last_folder_path` in `localStorage` and restores it on DOMContentLoaded; GET `/api/app-state` provides server-side state. Reset (Start over) clears the folder from app state and storage.
- **Status cache** (`shazam_cache.save_status_cache` / `load_status_cache`): Holds `to_download`, `have_locally`, `folder_stats`, **`urls`** (map of `"Artist - Title"` → Soundeo track URL), **`soundeo_titles`** (map of `"Artist - Title"` → exact track title/filename as shown on Soundeo), **`starred`** (map of `"Artist - Title"` → true if starred in Soundeo), **`dismissed`** (map of `"Artist - Title"` → true if user dismissed/unstarred on Soundeo), and **`dismissed_manual_check`** (list of track keys for which the user dismissed the Manual check message). When sync completes, merge new `urls`, `soundeo_titles`, and `starred` into current status and call `save_status_cache`. When rebuilding status from caches, copy `urls`, `soundeo_titles`, `starred`, `dismissed`, and `dismissed_manual_check` from the previous status cache so they survive refresh and restart.
- **Frontend**: On load, `restoreAppState()` runs first (folder path from localStorage + `/api/app-state`). Bootstrap (`/api/shazam-sync/bootstrap`) returns full status including `urls` and `starred`; `shazamApplyStatus(status)` sets `shazamTrackUrls` and `shazamStarred` so links and starred state show after refresh.
- Any new user-visible state (e.g. “marked as done”, custom labels) should be saved to an appropriate cache and included in the status or settings returned to the client.

## For AI agents

When adding or changing any feature in this app:

1. **Persist** every state change that results from an interaction (scan folder, fetched link, favorited, starred, skipped, compare result, settings change, etc.). Save after each such action. Nothing that the user sees or does should be lost on refresh or restart.
2. **Restore** that state when the page loads or when the API that serves the UI is called (bootstrap, status, app-state, etc.). Bootstrap and status must return full status including `urls` and `starred` so the frontend can restore links and starred state.
3. **Merge**, don’t overwrite, when combining new data with existing (e.g. new favorited URLs and starred keys merged into existing `urls` and `starred`).
4. Keep this doc and `AGENTS.md` in sync with any new persisted state.
