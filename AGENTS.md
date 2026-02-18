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

## Prefer Extended version (explicit rule)

**When multiple versions of a track exist (e.g. Original Mix, Extended Mix, Radio Edit), always prefer the Extended version.** This is integrated everywhere relevant:

- **Soundeo automation** (`soundeo_automation.py`): When choosing the best search result link, add a bonus to the match score for links whose text contains "extended" so Extended Mix/Version is chosen over Original Mix/Radio Edit when both match.
- **Local scanner** (`local_scanner.py`): When multiple local tracks match the same Shazam track (e.g. same normalized key or same canonical title), prefer the one whose title contains "extended" (exact-match map, canon pass, and fuzzy match collection all use `_prefer_extended_track` or equivalent).
- **Manual check**: Shown only when the synced Soundeo title indicates a non-extended version (e.g. "Original Mix", "Radio Edit") so the user can consider checking for an Extended version; not shown when the link is already Extended.

## Other

- Soundeo session (cookies) is saved to a path from config; sync and Save Session flow load it so login is remembered.
- Skip list is persisted in `shazam_skip_list.json` and applied when comparing.
