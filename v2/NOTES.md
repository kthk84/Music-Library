# SoundBridge – Notes

## Run the app

**Just run it yourself.** Use this command from the project folder:

```bash
python3 app.py
```

Or:

```bash
./run.sh
```

Then open **http://127.0.0.1:5002** in your browser.

## Undo dismiss (Sync tab)

Undo dismiss re-stars the track on Soundeo: it tries the HTTP API first, then falls back to the browser (same as the Star action) if the API doesn’t succeed (e.g. session/cookies). URL is resolved from status using key variants so the stored link is found.

## Orange dots (searched-not-found) persist after refresh

- **Dot state** (green = found, orange = searched not found, grey = not searched) is stored in `shazam_status_cache.json` via the `search_outcomes` log. That file is **persistent** (project root); it survives browser refresh and app restart.
- **Fix (Feb 2026):** Compare (and other code paths) were calling `save_status_cache(status)` with a status that had no `search_outcomes`, which overwrote the file and wiped the log. Orange dots then disappeared on refresh. In `save_status_cache` we now **preserve** the existing file’s `search_outcomes` when the status being saved has none, so the search log is never wiped and dots survive compare + refresh.

## Cover art — why thumbnails no longer vanish

The recurring "covers disappeared (after an interruption / Fetch / cancel)" bug
is permanently fixed. Root cause: `cover_hashes` was treated as precious state
hand-threaded through many rebuild/merge paths; any path that forgot it (or used
a mismatched key form) blanked covers even though the files were on disk — the
same shape as the orange-dots bug above, fixed the same band-aid way 7 times.

Now the **`cover_cache/` directory is the source of truth**: a cover is
`cover_cache/<md5(track_key_variant)>.jpg`, the map is recomputed from disk on
every status read (`/bootstrap` + `/status` both go through
`_overlay_disk_cover_hashes`), and the UI fetches covers **by track key** via
`/api/shazam-sync/cover-by-key` (server resolves the variant). A thumbnail shows
whenever the file exists — no persisted map can make it disappear. A blank cover
just means no cover file exists yet (track not searched/backfilled).

Full write-up: [`docs/COVER_ART_ARCHITECTURE.md`](docs/COVER_ART_ARCHITECTURE.md).
Tests: `tests/test_cover_art.py` (24 tests; the headline one reproduces the
post-interruption empty-map state and asserts `/status` rebuilds from disk).

## "Loading…" never finishing (background-tab render stall) — fixed

Symptom: the track list sometimes stuck on "Loading…" for a very long time
("up to two minutes"). Root cause was NOT slow rendering (a full ~3,500-row
render is ~460 ms): the render flush was scheduled only via
`requestAnimationFrame`, which browsers **pause in hidden/background tabs**. If
the app loaded while not the foreground tab, the first render never fired.

Fix: `shazamScheduleRenderTrackList` now races rAF against a 200 ms `setTimeout`
fallback (timers fire even when hidden); flush is idempotent. Verified rendering
3,297 rows in ~3.3 s in a hidden tab. Loading copy corrected to "Loading your
library…". See the perf section of `docs/COVER_ART_ARCHITECTURE.md`.

## Search all: browser vs HTTP, Stop button

- **No browser when you click Search:** Search can run in two ways. (1) **HTTP** — no Chrome window; uses cookies and requests. (2) **Browser** — opens Chrome; if **headed** you see the window, if **headless** it runs in the background. Config: `search_all_use_http` (true = no browser), `headed_mode` (false = headless). The progress bar now shows “Starting search (HTTP, no browser)…” or “Starting search (headless browser)…” so you can tell which mode you’re in.
- **Stop stays on “Stopping…”:** Fixed. When the batch actually stops, the progress poll now resets the Stop button back to “Stop” and hides the bar.
