# Download AIFF from Soundeo — feature spec (decided)

**Status:** In implementation.

---

## Decisions (user)

1. **UI:** Per-row **and** bulk download (both).
2. **Which tracks:** Only **blue dot** (Found on Soundeo) or **purple dot** (Starred on Soundeo) — i.e. `to_download` rows that have a Soundeo URL. **Green** (have locally + starred) and **teal** (have locally, not starred) → download button **grayed out**.
3. **Download discovery:** Evaluate the track page for HTTP/API (e.g. AIFF link in HTML or network request); crawler as fallback (click Download AIFF / intercept).
4. **Filename:** Keep the **original filename** from the download (as Soundeo provides). Map so it matches perfectly. If file exists: **add copy with number** (e.g. `filename.aiff` → `filename (1).aiff`).
5. **Download folder:** User selects **one of the destination folders** (used for scanning) as the Soundeo download destination. **Toggle or radio** so only one can be active.
6. **No credits:** When clicking download, if **redirect to https://soundeo.com/account/premium** → "To be able to download you need to purchase premium account." Detect this and show that message.
7. **Queue:** Sequential: each track is **downloaded then matched**, then move to next. Logging for each action; persist relevant info in **single source of truth** (status cache) next to logging.

---

## Implementation

- **Config:** `soundeo_download_folder` = one path from `destination_folders` (only one active).
- **API:** Set/get download folder; per-row download; bulk download queue (sequential).
- **Flow:** Discover AIFF URL (HTTP then crawler) → if redirect to `/account/premium` return no-credits error → save with original filename, copy-with-number if exists → rescan folder → match → log + save state to status cache.
- **Log:** Dedicated logger (e.g. `soundeo_download.log`) and key state in `shazam_status_cache.json` (e.g. `download_queue`, `download_results` or last run summary).

See STATE_PERSISTENCE.md: single source of truth for status is `shazam_status_cache.json`.
