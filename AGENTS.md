# Guidance for AI agents

When working on this codebase, follow these rules.

## State persistence (critical)

**All states, events, and status of the app must be saved after each interaction and remembered after the next load or refresh.** This applies to everything that happens in the app: if the user can see it or trigger it, it must persist across restart and refresh.

- After **every** user interaction (scan folder, fetch link, favorite, compare, skip, save session, change settings, etc.), **persist** the resulting state (e.g. `app_state.json`, `shazam_status_cache.json`, `shazam_skip_list.json`, or the appropriate store).
- On **every** page load or refresh (and after restarting the app), **restore** all persisted state so the UI shows the same data and status as before.
- Do not rely only on in-memory state for anything the user can see.
- **Fetched links** and **favorited/starred-in-Soundeo** state must be saved and restored: when a link is fetched or a track is favorited (starred) in Soundeo, that state must still be visible after refresh or restart.

See **[docs/STATE_PERSISTENCE.md](docs/STATE_PERSISTENCE.md)** for the full list of what we persist, where it is stored, and how to add new state.

## Shazam–Soundeo sync

- Compare results, “to download” list, “have locally” list, **favorited/fetched track URLs** (`urls`), and **starred-in-Soundeo per track** (`starred`) are stored in the status cache and must be returned by the status/bootstrap API so the frontend can restore them after refresh.
- When sync completes, merge new `urls` and `starred` from the sync result into the current status and call `save_status_cache(status)`.
- **Live dot update:** When per-row Search completes and a track is not found, the row dot must turn from grey to orange **without a page refresh**. The frontend does this by updating `shazamNotFound` and re-rendering; `shazamNotFound` is only replaced when applying fresh server data in `shazamApplyStatus`, never inside `shazamRenderTrackList`, or the per-row update is overwritten.
- **Batch search (Search new / Search unfound):** Dots update accordingly as the run progresses (progress poll merges `p.not_found` / `p.urls` / `p.soundeo_titles` and re-renders); on completion the final progress includes full `not_found`/`urls`/`titles` so the list is correct without a full page refresh. Star and action buttons follow the same state (urls/starred/soundeo_titles).

## Dot state: one-time wipe, then never again

- **One-time wipe (done):** All orange (searched-not-found) were cleared so every no-link track shows grey. No need to repeat.
- **From now on:** Every action that changes a dot (grey → orange, grey → link, orange → link) **must be saved immediately** via `save_status_cache(status)`. We never rely on in-memory state for dots; we never need to wipe again.
- **Backtrackable:** The current dot state is always in `shazam_status_cache.json` (`urls` + `not_found`). Refresh or restart restores that state, so it can always be backtracked to what was last saved.
- **No UI wipe button.** To turn all orange dots to grey (clear the not-found paper trail), run the script: `python3 scripts/reset_not_found.py`. Do not add a "Show all as grey" or similar button; the user wipes via script only.
- **Single source of truth only:** All state lives in **`shazam_status_cache.json`** — no separate log file, no extra storage. That file holds `to_download`, `have_locally`, `urls`, `not_found`, `starred`, `soundeo_titles`, `dismissed`, and **`search_outcomes`** (list of per-track search results: `{t, a, k, u}`). Dot state (`urls` / `not_found`) is derived from `search_outcomes` when we read, so the UI always reflects what actually happened.
- **Per-track tracking:** Every search outcome is appended to `status['search_outcomes']` in that same file. Single-track and batch search both save **as soon as** each result is known (found/not found) via `log_search_outcome` + `save_status_cache`, so restart mid-batch doesn’t lose progress and we never write a partial “all not_found” state. Replay (newest per key wins) gives `urls` and `not_found`. Rebuild-from-search-log (script or API) is a safety measure only.

## Prefer Extended version (explicit rule)

**When multiple versions of a track exist (e.g. Original Mix, Extended Mix, Radio Edit), always prefer the Extended version.** This is integrated everywhere relevant:

- **Soundeo automation** (`soundeo_automation.py`): When choosing the best search result link, add a bonus to the match score for links whose text contains "extended" so Extended Mix/Version is chosen over Original Mix/Radio Edit when both match.
- **Local scanner** (`local_scanner.py`): When multiple local tracks match the same Shazam track (e.g. same normalized key or same canonical title), prefer the one whose title contains "extended" (exact-match map, canon pass, and fuzzy match collection all use `_prefer_extended_track` or equivalent).
- **Manual check**: Shown only when the synced Soundeo title indicates a non-extended version (e.g. "Original Mix", "Radio Edit") so the user can consider checking for an Extended version; not shown when the link is already Extended.

## Other

- Soundeo session (cookies) is saved to a path from config; sync and Save Session flow load it so login is remembered.
- Skip list is persisted in `shazam_skip_list.json` and applied when comparing.
- **Track IDs for HTTP:** `status['track_ids']` (in `shazam_status_cache.json`) stores Soundeo numeric ID per track so star/unstar/dismiss use HTTP first; crawler is fallback when no ID. Backfill: `python3 scripts/backfill_track_ids.py`. IDs are also resolved on first interaction via `_resolve_track_id`.

## Where we left off (for next session)

- **Queue + row UX (done):** Unstar has full queue state (bar, row “Unstar queued 2/5”, × to remove). Spinner is always shown at the **start** of the row when a track is processing (current or pending); the hourglass in the actions cell was removed so **buttons stay visible** when the spinner is shown.
- **Possible next steps (not done):** (1) Log why we use crawling vs HTTP (e.g. “skipping HTTP: no track_id from URL” or “HTTP failed: …”) so we can see if HTTP is never tried or failing. (2) Set `track_id` when we first set a track URL (e.g. when Search returns a link) so every new track has both URL and ID without running the backfill script.
