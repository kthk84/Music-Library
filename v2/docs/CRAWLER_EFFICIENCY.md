# Crawler efficiency (Soundeo) – improvement ideas

**Status:** Improvement 1 and load_cookies reduction are **implemented**. Improvement 2 remains optional.

---

## Current flow (Run Soundeo / sync favorites) – after improvements

1. Open browser, load cookies (single base load; no second reload).
2. **No dedicated verify step:** first real navigation is favorites or search; login is inferred from that page (redirect → "session expired").
3. **Optionally crawl favorites:** navigate to `https://soundeo.com/account/favorites` (one or more pages), build `already_starred` from track keys found there.
4. For each track:
   - If key is in `already_starred`: do **not** open the track page; use search result URL only (avoids any star click).
   - If not in `already_starred`: open track detail page → if star button is **blue** (already favorited) do **not** click; if not blue, click to favorite.

Relevant code: `soundeo_automation.py` — `check_logged_in()`, `crawl_favorites_page()`, `run_favorite_tracks()`, `find_and_favorite_track()`, `_is_favorited_state()`.

---

## Improvement 1: Skip dedicated account page (verify login) — DONE

**Implemented:** No separate account page; login is inferred from the first real page.

- **load_cookies:** Only one `driver.get(SOUNDEO_BASE)` then add cookies; no second reload.

- **Favorites flows:** `run_favorite_tracks` and `crawl_soundeo_favorites` do not call a separate login check; they go straight to `crawl_favorites_page`. On page 1, `crawl_favorites_page` checks `_is_redirected_to_login(driver)` and returns `{"error": "not_logged_in", "favorites": []}`; callers return the same user-facing session-expired error.

- **Search flow:** `run_search_tracks`, single-row Sync, and single-row Search do not visit an account page first. `find_track_on_soundeo` and `find_and_favorite_track` check redirect after the first `driver.get(...)` and return `None` if redirected; callers infer "session expired" from `_is_redirected_to_login(driver)` when the result is None.

- **Explicit login check:** The "Check if logged in" and save-session verification use `check_logged_in(driver)`, which loads the favorites page and infers login from redirect (no account page).

- Helper: `_is_redirected_to_login(driver)` (logoreg, /login, /account/log in `current_url`).

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
