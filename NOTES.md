# MP3 Cleaner – Notes

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

## Search all: browser vs HTTP, Stop button

- **No browser when you click Search:** Search can run in two ways. (1) **HTTP** — no Chrome window; uses cookies and requests. (2) **Browser** — opens Chrome; if **headed** you see the window, if **headless** it runs in the background. Config: `search_all_use_http` (true = no browser), `headed_mode` (false = headless). The progress bar now shows “Starting search (HTTP, no browser)…” or “Starting search (headless browser)…” so you can tell which mode you’re in.
- **Stop stays on “Stopping…”:** Fixed. When the batch actually stops, the progress poll now resets the Stop button back to “Stop” and hides the bar.
