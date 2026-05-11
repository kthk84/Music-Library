# SoundBridge – Testing Strategy

## 1. Step-by-step approach: all known features

Features are grouped by **tab** and **user flow**. Each feature has a short **logic** note so testers know expected behaviour.

---

### A. Tab: Tags (MP3 metadata)

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| A1 | **Browse folder** | Click "Browse", pick folder | Folder path appears in input; path is persisted (app state). |
| A2 | **Scan folder** | Enter path (or use Browse) → "Scan" | Recursively finds MP3s; file list appears; stats (Total, Processed, Success) update; state persisted. |
| A3 | **Clean filenames** | Click "Clean filenames" (when alert shown) | Removes leading track numbers from filenames (e.g. `01. Song.mp3` → `Song.mp3`). |
| A4 | **Clean spam** | Click "Clean spam" (when alert shown) | Strips spam from metadata (comments, URLs, publisher junk). |
| A5 | **Lookup all** | Click "Lookup all" | For each file, calls MusicBrainz lookup; confidence badges and suggested metadata appear. |
| A6 | **Lookup single (auto)** | Click magnifier on a row | Same as lookup but for one file; auto-applies best result. |
| A7 | **Lookup single (choose)** | Click "Choose result" on a row | Opens result picker; user selects one result; metadata applied. |
| A8 | **Revert lookup** | Click "Revert" on a row | Restores previous metadata for that file (before last lookup). |
| A9 | **View metadata** | Click "Metadata" on a row | Shows current tag values (read-only). |
| A10 | **Save file** | Click "Save" on a row | Writes current metadata to that MP3 file. |
| A11 | **Save all** | Click "Save all" | Saves all files that have lookup/applied metadata. |
| A12 | **Reset** | Click "Reset" | Clears file list and app state (e.g. folder); user can start over. |
| A13 | **Play / progress** | Play button, progress bar, scrub | Plays selected file; progress updates; scrub seeks. |

---

### B. Tab: Sync (Shazam–Soundeo)

#### B.1 Settings & session

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B1 | **Load settings** | Open Sync tab | Bootstrap loads; destination folders, Soundeo cookie path, headed mode shown. |
| B2 | **Edit destination folders** | Type in folder inputs, Add folder, Remove (✕) | Folders list updates; Rescan / Save persists. |
| B3 | **Save settings** | Change folders (or other settings) → implicit save on Compare/Rescan | Settings persisted to config. |
| B4 | **Connect Soundeo / Save session** | Click "Connect Soundeo" → browser opens → log in → "I have logged in" | Cookies saved; session used for all Soundeo browser flows. |
| B5 | **Rescan folder** | Click "Rescan" next to a folder | Rescans that folder only; merge with Shazam; status and cache updated. |
| B6 | **Remove folder** | Click ✕ on a folder row | Folder removed from list; settings saved. |

#### B.2 Shazam & compare

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B7 | **Fetch Shazam** | Click "Fetch" | Reads Shazam DB; merges into `shazam_cache.json`; status updated (to_download from cache). |
| B8 | **Compare** | Click "Compare" | Scans destination folders; matches Shazam vs local; to_download / have_locally / counts; status and cache saved. |
| B9 | **Cancel compare** | Click "Stop" during compare | Stops scan; partial results may be shown. |
| B10 | **Rescan folders** | Click "Rescan folders" | Rescans all destination folders; re-compare; status updated. |
| B11 | **Filters** | All / Have / To DL / Skipped | Table filtered by status; selection bar only for To DL. |

#### B.3 Favorites range (Sync from Soundeo)

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B12 | **Sync favorites from Soundeo** | Click "All" / "1m" / "2m" / "3m" (Favorites) | Crawls Soundeo /account/favorites (pages by range); cross-checks app tracks not in crawl; merges starred/urls/titles; clears stale (unstarred on Soundeo); mutation log updated. |
| B13 | **Progress & Stop** | During sync favorites | Progress bar shows message; Stop requests halt after current operation. |

#### B.4 Track list row actions

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B14 | **Search on Soundeo (row)** | Click search icon on a row | Browser: search only, no favorite; URL and soundeo title stored; row shows link/play when found. |
| B15 | **Find & star (row)** | Click sync/link icon on a row (To DL, no link) | Browser: search + open + click favorite; url/starred/soundeo_titles stored; row updates. |
| B16 | **Dismiss (row)** | Click dismiss (X) on a row | Unfavorites on Soundeo (if had URL); marks dismissed locally; row moves to "dismissed" state. |
| B17 | **Undo dismiss** | Click "Undo" on dismissed row | Re-favorites on Soundeo (if URL); clears dismissed; row back to To DL. |
| B18 | **Skip (row)** | Click skip icon on To DL row | Adds to skip list; track removed from to_download; appears in Skipped filter. |
| B19 | **Undo skip** | Click "Undo" on skipped row | Removes from skip list; track back in to_download. |
| B20 | **Dismiss Manual check** | Click manual-check icon (Original/Radio only) | Dismisses "check for Extended" hint for that track; persisted. |
| B21 | **Play local** | Play button (when have file) | Streams local file; player bar. |
| B22 | **Play Soundeo** | Play button (when have link, no file) | Streams preview from Soundeo (or opens link). |
| B23 | **Starred indicator** | — | Filled star = in Soundeo favorites (from crawl/cross-check); outline = not or unknown. Tooltips explain. |

#### B.5 Bulk actions (selection)

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B24 | **Select tracks** | Checkboxes on To DL rows | Selection count shown; enables Skip / Sync selection. |
| B25 | **Select all** | Header checkbox | Toggles all To DL rows. |
| B26 | **Skip selected** | Click "Skip" in selection bar | Skips all selected tracks (add to skip list; remove from to_download). |
| B27 | **Sync selected** | Click "Sync" in selection bar | Run Soundeo for selected tracks only (find & star). |

#### B.6 Global Soundeo actions

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B28 | **Search all on Soundeo** | Click "Search all" in toolbar | For each track (to_download + have_locally) without a URL: search Soundeo (no favorite); store url/title; skip already-found. Progress shown. |
| B29 | **Run Soundeo (Sync)** | Select time range, click "Sync" | Crawl favorites first; then for each selected/to_download track: find & star on Soundeo (browser). Progress shown; urls/starred/soundeo_titles merged. |
| B30 | **Stop sync** | Click "Stop" during Run Soundeo or Search all | Requests stop; finishes current track then stops. |

#### B.7 Remove from Soundeo

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B31 | **Remove from Soundeo** | (Where offered) Unfavorite on Soundeo | Opens track URL in browser; clicks favorite to unfavorite; local status updated (starred false). |

#### B.8 Exports & mutation log

| # | Feature | User action | Logic / expected behaviour |
|---|--------|-------------|----------------------------|
| B32 | **Export local filenames** | (If present) Export link | Downloads list of scanned local filenames. |
| B33 | **Export Shazam tracks** | (If present) Export link | Downloads list of Shazam tracks. |
| B34 | **Mutation log** | GET /api/shazam-sync/mutation-log | Returns recent starred/unstarred mutations (for debugging/support). |

---

### C. State persistence (cross-cutting)

| # | Feature | Logic / expected behaviour |
|---|--------|----------------------------|
| C1 | **After any interaction** | Relevant state saved (status cache, skip list, config, app state). |
| C2 | **On load / refresh** | Bootstrap and app-state restore: folders, compare result, urls, starred, dismissed, skip list, filters. |
| C3 | **Merge, not overwrite** | New sync results merge into existing urls/starred/soundeo_titles. |

---

### D. API surface (for automation / smoke tests)

| # | Endpoint / area | Purpose |
|---|-----------------|--------|
| D1 | GET `/` | Page load. |
| D2 | GET `/api/app-state` | App state (folder, etc.). |
| D3 | GET `/api/settings` | Settings (folders, Soundeo path, etc.). |
| D4 | GET `/api/shazam-sync/bootstrap` | Settings + full status (for Sync tab). |
| D5 | GET `/api/shazam-sync/status` | Status only. |
| D6 | GET `/api/shazam-sync/progress` | Current sync/search progress. |
| D7 | POST `/api/scan-folder` | Scan folder (Tags). |
| D8 | POST `/api/shazam-sync/compare` | Compare Shazam vs local. |
| D9 | POST `/api/shazam-sync/sync-favorites-from-soundeo` | Start favorites sync. |
| D10 | POST `/api/shazam-sync/search-soundeo-single` | Search one track (no favorite). |
| D11 | POST `/api/shazam-sync/search-soundeo-global` | Search all without link. |
| D12 | POST `/api/shazam-sync/sync-single-track` | Find & star one track (browser). |
| D13 | POST `/api/shazam-sync/run-soundeo` | Run full Soundeo sync (selected or to_download). |
| D14 | POST `/api/shazam-sync/stop` | Request stop. |
| D15 | GET `/api/shazam-sync/mutation-log` | Mutation log. |

---

## 2. Double-check: features and logic

- **Tags tab:** Browse, Scan, Clean filenames, Clean spam, Lookup (all/single auto/single choose), Revert, View metadata, Save (file/all), Reset, Play/scrub. **All listed (A1–A13).**
- **Sync tab:** Settings (folders, save, Rescan folder, Remove folder), Connect Soundeo / Save session, Fetch, Compare, Cancel compare, Rescan folders, Filters, Favorites range + Sync favorites, row actions (Search, Find & star, Dismiss, Undo dismiss, Skip, Undo skip, Manual check dismiss, Play local/Soundeo, Starred), selection (Select all, Skip selected, Sync selected), Search all, Run Soundeo, Stop, Remove from Soundeo, Exports, Mutation log. **All listed (B1–B34, D).**
- **Persistence:** Save after actions; restore on load; merge on sync. **Listed (C1–C3).**
- **Logic:** Each row has a short “expected behaviour” so testers can validate correctly.

---

## 3. Test plan per feature

### 3.1 Tags (A1–A13)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| A1 | Browse folder | Browse → select folder | Path in input; persisted after refresh. | Manual |
| A2 | Scan folder | Set path → Scan | File list; stats; state persisted. | Manual |
| A3 | Clean filenames | Trigger alert (e.g. "01. x.mp3") → Clean filenames | Leading numbers removed. | Manual |
| A4 | Clean spam | Trigger spam alert → Clean spam | Spam fields cleared. | Manual |
| A5 | Lookup all | After scan → Lookup all | Each row gets suggestion + confidence. | Manual |
| A6 | Lookup single auto | Click magnifier on one row | One lookup; best result applied. | Manual |
| A7 | Lookup single choose | Click Choose result → pick one | Picked result applied. | Manual |
| A8 | Revert | After lookup → Revert | Metadata back to pre-lookup. | Manual |
| A9 | View metadata | Click Metadata | Modal/content with current tags. | Manual |
| A10 | Save file | Change/lookup → Save on row | File on disk updated. | Manual |
| A11 | Save all | Multiple changed → Save all | All changed files written. | Manual |
| A12 | Reset | Reset | List cleared; folder cleared in state. | Manual |
| A13 | Play | Play → progress/scrub | Audio plays; progress/scrub work. | Manual |

### 3.2 Sync – Settings & session (B1–B6)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| B1 | Load settings | Open Sync tab | Folders and options from bootstrap. | Smoke (bootstrap) |
| B2 | Edit folders | Add/remove/edit folder paths | List and persisted config match. | Manual |
| B3 | Save settings | Change folders → trigger save (e.g. Compare) | After refresh, folders unchanged. | Manual |
| B4 | Save Soundeo session | Connect → log in → I have logged in | No session error on next sync/search. | Manual |
| B5 | Rescan folder | Rescan one folder | That folder rescanned; status updated. | Manual |
| B6 | Remove folder | Remove folder | Gone from list and config. | Manual |

### 3.3 Sync – Shazam & compare (B7–B11)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| B7 | Fetch Shazam | Fetch | Shazam count > 0 (if DB has data); cache updated. | Smoke (bootstrap) |
| B8 | Compare | Set folders + Fetch → Compare | to_download / have_locally / counts; status persisted. | Manual |
| B9 | Cancel compare | Compare → Stop quickly | Stops; partial or empty result. | Manual |
| B10 | Rescan folders | Rescan folders | All folders rescanned; compare redone. | Manual |
| B11 | Filters | Switch All/Have/To DL/Skipped | Table shows correct subset. | Manual |

### 3.4 Sync – Favorites & row actions (B12–B23)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| B12 | Sync favorites | All/1m/2m/3m → crawl runs | Progress; starred/urls match Soundeo; unstarred cleared. | Manual |
| B13 | Progress & Stop | Start sync → Stop | Progress shows; then stops. | Manual |
| B14 | Search on Soundeo (row) | Click search on row without link | Browser searches; link appears on row; no star. | Manual |
| B15 | Find & star (row) | Click Find & star on To DL row | Browser finds and favorites; row gets link + star. | Manual |
| B16 | Dismiss | Dismiss row with link | Unstarred on Soundeo; row dismissed. | Manual |
| B17 | Undo dismiss | Undo on dismissed | Starred again; row back to To DL. | Manual |
| B18 | Skip | Skip To DL row | In skip list; row in Skipped filter. | Manual |
| B19 | Undo skip | Undo on skipped | Back in to_download. | Manual |
| B20 | Dismiss Manual check | Click manual-check icon | Hint gone; persisted. | Manual |
| B21 | Play local | Play on have-local row | Local file plays. | Manual |
| B22 | Play Soundeo | Play on row with link only | Preview or link works. | Manual |
| B23 | Starred indicator | Compare with Soundeo starred state | Filled/outline star and tooltips correct. | Manual |

### 3.5 Sync – Bulk & global (B24–B31)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| B24–B25 | Select / Select all | Check rows; header checkbox | Count and selection correct. | Manual |
| B26 | Skip selected | Select → Skip | All selected skipped. | Manual |
| B27 | Sync selected | Select → Sync | Only selected run through Soundeo. | Manual |
| B28 | Search all | Search all | Tracks without link get link (no star); progress; skip already-found. | Manual |
| B29 | Run Soundeo | Sync with range | Favorites crawl + find & star for tracks; progress; merged state. | Manual |
| B30 | Stop sync | Run Sync or Search all → Stop | Stops after current. | Manual |
| B31 | Remove from Soundeo | Unfavorite flow | Track unfavorited; local starred false. | Manual |

### 3.6 Persistence (C1–C3)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| C1 | Save after action | Do Compare / Sync / Skip / Settings | Reload; state same. | Manual |
| C2 | Restore on load | Refresh after Compare + some sync | Status, urls, starred, filters restored. | Manual |
| C3 | Merge on sync | Sync favorites then Run Soundeo (or vice versa) | Both url and starred data present; no wipe. | Manual |

### 3.7 API / smoke (D)

| Id | Test | Steps | Pass criteria | Automated? |
|----|------|--------|----------------|------------|
| D1–D6 | Page + key GETs | GET /, /api/app-state, /api/settings, bootstrap, status, progress | 200; valid JSON where applicable. | Smoke + pytest |
| D7–D15 | Key POSTs/GETs | Compare, sync-favorites, search-single, search-global, sync-single-track, run-soundeo, stop, mutation-log | 200 or 4xx with body; no 500 on valid input. | Smoke (GETs only); POSTs manual |

---

## 4. How to run tests

- **Unit tests (pytest):** From project root: `python3 -m pytest tests/ -v`. No server required. Covers matching, local_scanner, Shazam reader.
- **Smoke test (app must be running):** Start the app (`python app.py`), then run `bash test_app.sh`. The script uses port **5002** by default (same as `app.py`). Override with `MP3CLEANER_PORT=5002 bash test_app.sh`. Smoke test hits: `/`, `/api/settings`, `/api/shazam-sync/bootstrap`, `/api/shazam-sync/status`, `/api/shazam-sync/mutation-log`.
- **Manual:** Follow the test plan tables in section 3; mark Pass/Fail per Id; note environment (e.g. Soundeo session saved, Shazam DB present).

---

## 5. Validation and test execution

- **Manual:** Follow test plans above; mark Pass/Fail per Id; note environment (e.g. “Soundeo session saved”, “Shazam DB present”).  
- **Automated smoke:** Use `test_app.sh` (bootstrap, settings, page load); (bootstrap, settings, page load, status, mutation-log).  
- **Regression:** After changes, re-run the test plan for affected features and persistence.

---

## 6. Document ownership

- Keep this file in sync when adding features or changing behaviour.  
- When adding a feature: add to section 1 (with logic), add to section 2 check, add test plan row in section 3.
