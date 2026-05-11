# Next steps and open decisions

**Last updated:** After logging + _set_url_and_track_id wiring.

---

## Run status

### Backfill script
- **Command:** `python3 scripts/backfill_track_ids.py`
- **Status:** Done. Backfilled 5340 track_id(s), 0 skipped/failed. Status cache updated. (Run completed in ~3.5 min.)

---

## What you have not decided yet (your call)

1. **Logging “why crawling vs HTTP”**  
   - **Option:** Add log messages when we skip HTTP (e.g. “no track_id”) or when HTTP fails (e.g. session/API error) so you can see in logs whether HTTP was never tried or failed.  
   - **Decision:** Do you want this logging added, or skip it?

2. **Nothing else** — the rest below are implementation tasks, not product decisions.

---

## What’s still to do (implementation)

### 1. Wire `_set_url_and_track_id` everywhere we set a URL (recommended)
- **Goal:** Whenever we set a track URL (search, sync, merge, etc.), also resolve and store the track ID so new data has both URL + ID without running the backfill again.
- **Current:** `_set_url_and_track_id(status, key, url, cookies_path)` exists in `app.py` but is **not** used at the places that assign URLs. Those places still do `status['urls'][key] = url` (and sometimes `status['urls'][key.lower()] = url`).
- **Call sites to update** (replace direct `status['urls'][...] = url` with `_set_url_and_track_id(status, key, url, cookies_path)` and pass `cookies_path` where missing):
  - **Merge crawled favorites into status** (`_store` / `_merge_crawled_favorites_into_status`) — around lines 2296–2297, 2403.
  - **Run favorite tracks** — merge loop when setting `new_urls` (around 2592).
  - **Sync single** — when setting URL after sync.
  - **Single-track search** — `_run_search_soundeo_single` when setting URL (around 2674).
  - **Batch search** — `on_progress` / progress merge when “Found” (around 2797–2798; may need to pass status + cookies_path into progress handler).
  - **Undismiss / star** — when setting a new URL for a track.
- **Effect:** New links from search/sync will get both URL and ID; star/unstar/dismiss can use HTTP more often without a separate backfill.

### 2. Optional: “Why crawling vs HTTP” logging
- Only if you decide you want it (see “What you have not decided yet” above).
- Add short log lines at the places we choose crawler vs HTTP (e.g. “skipping HTTP: no track_id” or “HTTP failed: …”).

---

## Already done (no action needed)

- **Search starred state after manual unstar on Soundeo:** Single search uses the same HTTP `soundeo_api_get_favorite_state` as star/unstar. We removed the "avoid false unstar" overwrite that kept `starred=True` when the detector said False — so when the user unstars a track on Soundeo and searches again, the app now correctly shows unstarred (gray star). Confirmed working.
- Unstar queue (bar, row “Unstar queued X/Y”, × remove).
- Spinner at row start when processing; no hourglass in actions; buttons stay visible.
- Track IDs: storage in status cache, backfill script, use in star/unstar/dismiss/undismiss, `_resolve_track_id`, `_set_url_and_track_id` (implementation exists; wiring at all URL-setting call sites pending).
- Crawler efficiency: one base load in `load_cookies`, no dedicated account page, login inferred from first real page (favorites or search).
- State persistence and docs: `track_ids`, `urls`, `starred`, etc. documented in `STATE_PERSISTENCE.md` and `AGENTS.md`.

---

## Next important feature: Download AIFF from Soundeo

- **Goal:** Both HTTP and crawler should support **downloading the file (AIFF)** from Soundeo. User sets a **local download folder** in Settings; after download, **scan** the file so it's part of the list and can be matched.
- **Constraint:** Downloads only work when the Soundeo subscription is active (currently not active).
- **Doc:** [docs/DOWNLOAD_AIFF_FEATURE.md](DOWNLOAD_AIFF_FEATURE.md) — outline, integration points, and **clarifications needed** (UI placement, how Soundeo exposes the download URL, filename policy, subscription detection, etc.) before implementation.

---

## Suggested next step

1. **Decide** whether you want the “why crawling vs HTTP” logging (yes/no).
2. **Implement** wiring of `_set_url_and_track_id` at all URL-setting call sites above so every new URL also gets a track ID.
3. **Let the backfill finish** (or run it again later); it only backfills IDs for existing URLs and is safe to re-run.

If you want, we can do step 2 next and then add the optional logging only if you choose yes for step 1.
