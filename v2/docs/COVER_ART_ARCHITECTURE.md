# Cover-Art Architecture (and why thumbnails used to keep vanishing)

> TL;DR — The `cover_cache/` directory on disk is the **single source of truth**.
> A cover is `cover_cache/<md5(track_key_variant)>.jpg`. The key→file mapping is
> therefore *derived* and fully reconstructable from disk. We recompute it on
> every status read and serve covers **by track key**, so a thumbnail shows
> whenever the file exists — no persisted map can make it disappear.

## The bug this design kills

For months, cover thumbnails kept disappearing — "after an interruption", after a
Fetch, after a cancelled Compare, seemingly at random. It was "fixed" at least
**seven times** (`22d119c`, `1cfdbe5`, `575f232`, `7cef87d`, `5d173a9`,
`311b429`, `e5074f5`) and kept coming back.

Every one of those was a band-aid, because they all shared one root cause:

**`cover_hashes` (track key → md5 hash) was treated as precious, authoritative
state that had to be hand-threaded through ~4 server status-rebuild paths and
~4 client merge paths — each of which had to independently *remember* to carry
it forward, under a matching key normalization.** `save_status_cache`, the one
chokepoint every write passes through, didn't even know `cover_hashes` existed.

So any code path that rebuilt status without re-attaching `cover_hashes` — an
interrupted/partial write, a cancelled-compare bare dict, a stale-cache Fetch, a
`.bak` restore that omitted the field — produced a status with an empty or
sparse map, and the UI (which pointed `background-image` at
`/cover/<hash>`) had nothing to render. The files were sitting right there in
`cover_cache/`; the UI just didn't know their hashes.

A second, intertwined failure: **key-normalization drift.** Covers were stored
under up to 6 key variants; lookups tried a *different* variant set; the playbar
tried yet another. A cover cached under `"davi, definition - desole"` wouldn't be
found when the row's key was `"Davi & Definition - Désolé"`.

## The permanent fix (the invariant)

> **The served cover map is recomputed from the `cover_cache/` directory on every
> status read, and covers are fetched by track key. Display depends only on what
> is on disk — never on what was persisted.**

This makes the entire "map went sparse / drifted" bug class *impossible by
construction*, not merely patched at one more call site.

### Components

1. **`cover_key_variants(key)` — the ONE canonical normalizer** (`lib/covers.py`).
   Storage, lookup, and disk-derivation all go through it, so a cover cached
   under any variant is resolvable under all of them. (Order: exact, lowercase,
   parens-stripped ±lc, deep-normalized ±lc.) This kills the normalization-drift
   failure.

2. **`compute_cover_hashes_from_disk(status)`** (`lib/covers.py`). Reconstructs a
   *complete* `cover_hashes` map by hashing the track keys present in `status`
   (urls, starred, and the `have_locally`/`to_download`/`maybe`/`listened`/
   `skipped` rows' `"Artist - Title"`) and checking which hashes exist on disk.
   Maps each hit under the row's **primary key** *and lowercase* (so the overview
   render always hits) **and** the matched variant (so the playbar hits). Uses a
   memoized directory listing — cheap to call at the ~1 Hz poll rate.

3. **`_overlay_disk_cover_hashes(status)`** (`app.py`). The read chokepoint:
   called by **both** `/api/shazam-sync/bootstrap` (initial load) and
   `/api/shazam-sync/status` (polls). Overlays the disk-derived map onto the
   outgoing status. After this, the served map can never be sparser than disk.

4. **`/api/shazam-sync/cover-by-key?key=…`** (`app.py`). The resilient fetch
   path. The frontend passes the track key it always has; the server resolves it
   to a file via `find_cover_file_for_key` (canonical variants). A cover renders
   whenever the file exists, regardless of which variant it was cached under or
   whether the map's key form matches the row exactly. (Security: the path is
   `md5(key)` hex joined to `cover_cache`, so no key content can escape the dir.)
   The legacy `/cover/<hash>` route is retained for back-compat.

5. **Frontend** (`static/app.js`). The overview cover cell and the playbar fetch
   via `cover-by-key` keyed on the row/bar track key. The map is now only an
   *existence gate* (decides cover vs placeholder), and it is disk-complete.

### Defense in depth — persistence hardening (`shazam_cache.py`)

Display no longer depends on the persisted map, but we keep the on-disk JSON sane
anyway (helps every other reader, reduces churn):

- **`save_status_cache`** merges `cover_hashes` from the existing file — a save
  whose status dropped the map can never shrink what's persisted. This is the
  single write-chokepoint guard the ~40 individual save sites no longer each
  have to remember.
- **`load_status_cache`** includes `cover_hashes` in its `.bak` recovery list.
- **`_save_json_atomic`** never truncates the live file in place — both the
  primary and fallback writes are temp-file + `os.replace`, so an interrupted
  write can only leave a stray `.tmp`, never a corrupt status (a corrupt status
  reads back as `None` and would wipe *everything*, covers included).

## Why a cover can still be (correctly) absent

A blank cover cell is correct when **no cover file exists** for that track —
typically a freshly-Shazammed track not yet searched/backfilled, or a track with
no Soundeo source (grey/orange dot). Covers are cached during Soundeo search /
the backfill job; the architecture *shows* covers that exist, it does not
fabricate missing ones. To populate missing covers, run a search or
`POST /api/shazam-sync/backfill-covers`.

## Tests

`tests/test_cover_art.py` pins the invariant. The headline regression —
`test_status_recomputes_cover_hashes_from_disk_when_persisted_map_empty` —
reproduces the exact post-interruption state (persisted `cover_hashes: {}`, file
on disk) and asserts `/status` heals it. It would have failed on all 7 prior
band-aid commits. Companion tests cover variant resolution (accents/`&`/parens),
the by-key endpoint (serve/404/400/traversal-safety), disk-derivation, and the
three persistence-hardening guards.

## Performance note (separate, pre-existing)

Rendering the **full** unfiltered list (~3,500 rows of string-concatenated HTML)
is slow — the app shows "Loading… (large libraries can take up to two minutes)".
That is a pre-existing client-render cost unrelated to covers (the server's
`/bootstrap` and `/status` return in ~2 s; `cover-by-key` in ~1 ms). A future
improvement would be row virtualization or lazy/incremental rendering. Filtering
(search box, time range) renders a subset instantly.
