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

## Other

- Soundeo session (cookies) is saved to a path from config; sync and Save Session flow load it so login is remembered.
- Skip list is persisted in `shazam_skip_list.json` and applied when comparing.
