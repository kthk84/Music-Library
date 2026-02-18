# Crawler efficiency (Soundeo) – improvement ideas

**Status:** Documented for later implementation. Not yet implemented.

---

## Current flow (Run Soundeo / sync favorites)

1. Open browser, load cookies.
2. **Verify login:** navigate to `https://soundeo.com/account`; if redirected to login → fail; else continue.
3. **Optionally crawl favorites:** navigate to `https://soundeo.com/account/favorites` (one or more pages), build `already_starred` from track keys found there.
4. For each track:
   - If key is in `already_starred`: do **not** open the track page; use search result URL only (avoids any star click).
   - If not in `already_starred`: open track detail page → if star button is **blue** (already favorited) do **not** click; if not blue, click to favorite.

Relevant code: `soundeo_automation.py` — `verify_logged_in()`, `crawl_favorites_page()`, `run_favorite_tracks()`, `find_and_favorite_track()`, `_is_favorited_state()`.

---

## Improvement 1: Skip dedicated account page (verify login)

**Idea:** Do not navigate to `/account` only to verify login. Infer login from the **first real navigation** (e.g. favorites list or search).

**Why it helps:** Saves one page load at the start of every crawler/sync run.

**How:** The same redirect check used in `verify_logged_in()` (e.g. `logoreg`, `/login`, `/account/log` in `current_url`) is already done in `crawl_favorites_page()` on page 1 (lines 774–778). So:

- When **crawl_favorites_first=True**: go straight to the first favorites page; if redirected to login, treat as “not logged in” and return the same error.
- When the first action is search (e.g. no crawl): go to search or track list; if redirected to login, treat as not logged in.

**Implementation notes:** Remove or bypass the initial `verify_logged_in(driver)` call and perform the same URL/redirect check after the first real `driver.get(...)` in the flow. Keep the same user-facing error message (“Session expired or logged out…”).

---

## Improvement 2: Skip favorites listing for “already starred” (optional, tradeoff)

**Idea:** Do not crawl `/account/favorites` to build `already_starred`. Rely only on the **track detail page**: when we open a track, check if the star is blue; if blue = already favorited, do not click (avoids unstarring).

**Current safety:** The app already **never** clicks the star when it’s blue on the detail page (`_is_favorited_state()` in `find_and_favorite_track()`). So unstarring is already avoided even without the crawl.

**Efficiency tradeoff:**

| Approach | Page loads |
|----------|------------|
| **Current** | 1 (account) + K (favorites pages) + (N − M) track pages. N = tracks to process, M = already starred (from crawl). |
| **Skip account + favorites** | N track pages (no account, no favorites crawl). |

- **Saved:** 1 + K page loads.
- **Extra:** M track page loads (one per already-starred track that we no longer skip opening).

So skipping the favorites listing **improves** when `1 + K > M` (e.g. few tracks already starred, or many favorites pages). It can **worsen** when many tracks are already starred (large M) and K is small.

**State / source of truth:** Today the favorites crawl also produces `crawled_favorites` → `_merge_crawled_favorites_into_status()` so `starred`, `urls`, and `soundeo_titles` stay in sync. If we skip the crawl entirely for Run Soundeo, we no longer get that bulk list from the listing page. Options:

- Update `starred`/`urls`/`soundeo_titles` only from detail-page outcomes (and from a separate “Sync favorites from Soundeo” crawl when that runs).
- Or keep an optional crawl (e.g. configurable or only for “Sync favorites”) and only skip it for the “Run Soundeo” flow when we want to favor speed.

---

## Star/unstar logic (reference)

- **Starring:** We only click (or press F) when `_is_favorited_state(btn)` is **false**. So we never unstar from the “find and favorite” flow.
- **Unstarring:** Done only in explicit flows (`unfavorite_track_on_soundeo`, dismiss, etc.), and only when the button is in favorited state before clicking.
- **already_starred:** Used so we don’t open the track page for known favorites; it’s an optimization. If we remove the favorites crawl, we lose that optimization for already-starred tracks, but the detail-page check still prevents unstarring.

See `AGENTS.md` and `docs/STATE_PERSISTENCE.md` for how `starred`, `urls`, and `soundeo_titles` are persisted and restored.
