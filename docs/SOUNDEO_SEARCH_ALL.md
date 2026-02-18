# How "Search all" works (Soundeo)

## Short answer

**Search all uses the website via a browser (Selenium/Chrome), not a Soundeo API.** It opens Chrome, loads your saved session cookies, and for each track without a link it visits the Soundeo track list page with a search query and scrapes the result links.

## Design: global vs per-row search

- **Search all (global)** only runs for tracks that **don’t have a Soundeo URL yet**: either **not found** (we searched before and got nothing) or **new** in the overview (never looked for). Tracks that already have a link are skipped so we avoid unnecessary searches.
- **Per-row Search** (magnifier on a row) is for **re-search of a single track** when you need it (e.g. to find a different link or try again). That is rare; normally global Search all covers “no URL” tracks only.

## Flow

1. **Frontend:** You click **Search all** → `POST /api/shazam-sync/search-soundeo-global`.
2. **Backend:** A background thread runs `_run_search_soundeo_global()` in `app.py`, which:
   - Builds a list of tracks that don’t have a Soundeo URL yet (from `to_download` + `have_locally`).
   - Calls `run_search_tracks()` in `soundeo_automation.py`.
3. **soundeo_automation:**
   - Starts Chrome (headed or headless from Settings → “Headed mode”).
   - Loads cookies from the path in config (same as “Save session”).
   - Verifies you’re logged in (`verify_logged_in()`).
   - For each track: `find_track_on_soundeo(driver, artist, title)`:
     - Builds search queries (e.g. `"Artist Title"`, `"Title Artist"`, `"Artist"`, `"Title"`).
     - For each query: `driver.get(https://soundeo.com/list/tracks?searchFilter=...&availableFilter=1)`.
     - Finds links with Selenium: `a[href*="/track/"]`.
     - Scores results (artist/title match + “Extended” bonus) and returns the best (url, display text).
   - Saves found URLs/titles into the status cache and merges into the UI.

So it is **crawling the website with a browser**, not calling a private API.

## Why it might “not work”

| Cause | What to check |
|-------|----------------|
| **No saved session** | Use **Connect Soundeo** → log in → **I have logged in**. Search all needs the same cookie file. |
| **Session expired** | Save session again; ensure cookies file path in Settings is correct. |
| **All tracks already have links** | UI will show “No tracks to search (all have links).” |
| **Chrome/driver issues** | Ensure Chrome is installed; try with **Headed mode** on so you see the browser. If Chrome closes or crashes, you’ll see an error in the progress area. |
| **Soundeo changed their page** | If Soundeo changed HTML or moved to heavy JavaScript, the selector `a[href*="/track/"]` might not find links. Then Search all would report “Not found” for many tracks. |
| **Rate limiting / block** | Delays are 1.5–2.5 s between requests; if Soundeo throttles or blocks, you may see failures. |

## HTTP search (no browser) — optional

The codebase has an **HTTP-based** search that uses the same URL but with `requests` + cookies and regex on the HTML: `soundeo_api_search()` in `soundeo_automation.py`. It is used for **Undismiss** (re-favorite) and similar flows, **Search all** can use it: in config.json set "search_all_use_http": true for HTTP (no Chrome). Same cookies required. If we add an option to run “Search all” via this HTTP path, it would work without opening Chrome but would depend on the current HTML regex; if Soundeo change the page structure, that path could break too.

## Quick checks

- **Single track:** Use **Search on Soundeo** (magnifier) on one row. If that fails too, the problem is session or site structure, not only “Search all”.
- **Progress/errors:** Watch the progress text and any error message after Search all (e.g. “Soundeo session expired”, “No saved session”, “Browser was closed”).
- **Headed mode:** In Sync settings, turn **Headed mode** on and run Search all again; you should see Chrome open and navigate. That confirms the browser path is running and may show login or captcha issues.
