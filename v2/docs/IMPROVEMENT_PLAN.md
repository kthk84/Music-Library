# SoundBridge – Improvement Plan (post-testing)

Based on: testing strategy, test plan, validation (pytest + smoke), and gaps identified.

---

## 1. Gaps and observations

| Area | Finding |
|------|--------|
| **Smoke test** | `test_app.sh` assumes app on **port 5002**; README says **5000**. Inconsistent. |
| **API coverage** | Smoke test only hits `/`, `/api/settings`, `/api/shazam-sync/bootstrap`. No coverage for status, compare, mutation-log, Tags APIs. |
| **Test docs** | No "how to run tests" or "expected environment" in TESTING_STRATEGY.md. |
| **Regression** | No automated checklist linking test plan IDs to CI or pre-commit. |
| **Unit tests** | Strong coverage for matching, local_scanner, Shazam reader; no tests for Flask routes or persistence helpers. |

---

## 2. Improvement plan (prioritised)

### P1 – Quick wins (do first)

1. **Document test execution**  
   In `docs/TESTING_STRATEGY.md`, add a short "How to run tests" section: run pytest (`python3 -m pytest tests/ -v`), run smoke test (`bash test_app.sh`), and note that smoke test expects the app on **5002** (or make port configurable).

2. **Clarify app port**  
   Either: (a) document in README that smoke test uses 5002 and how to start app on 5002, or (b) make `test_app.sh` use a configurable port (e.g. env `MP3CLEANER_PORT=5002` default) and document it. Avoid leaving README at 5000 and script at 5002 without explanation.

3. **Extend smoke test**  
   In `test_app.sh`, add one or two more cheap checks: e.g. GET `/api/shazam-sync/status` and GET `/api/shazam-sync/mutation-log`. Both should return 200 and valid JSON when the app is up. This catches regressions on these endpoints without needing a full compare/sync.

### P2 – Nice to have

4. **Test plan traceability**  
   Add a one-line "Automated?" column to the test plan in TESTING_STRATEGY.md: which test IDs are covered by pytest, which by smoke, which are manual only. Helps future contributors see coverage at a glance.

5. **Persistence sanity check**  
   Add a small pytest that (using temp dirs and mock data) calls the status cache save/load and merge helpers (or the minimal logic that rebuilds status from caches) to ensure we don’t break merge/restore logic. Optional if time is short.

### Future improvements (documented elsewhere)

- **Crawler efficiency (Soundeo):** See `docs/CRAWLER_EFFICIENCY.md` for ideas on skipping the account-page login check and optionally the favorites listing, using the track detail page star state (blue = already favorited) to avoid extra navigations.

### P3 – Out of scope for this pass

6. **Flask route tests**  
   Full route testing (e.g. POST compare with fixtures) would require more setup (DBs, folders, mocks). Defer; manual test plan already covers behaviour.

7. **E2E / browser**  
   No Playwright/Selenium in this pass; Sync tab and Soundeo flows remain manual.

---

## 3. Success criteria

- Anyone can run tests by following TESTING_STRATEGY.md.
- Port 5000 vs 5002 is documented and consistent (or script is configurable).
- Smoke test covers at least status and mutation-log in addition to bootstrap/settings/page.
- Improvement plan is reviewed critically (see next section) and only P1 (+ optionally part of P2) executed in this cycle.

---

## 4. Critical review (self-review)

- **Scope:** P1 is small and contained; P2 is optional; P3 explicitly deferred. Risk of scope creep is low.
- **Value vs effort:** P1 gives immediate value (clear docs, less confusion, better regression signal). P2 traceability is low effort; persistence pytest is medium effort.
- **Risks:** Making the script port-configurable might require checking how the app is started (e.g. `app.py` or env). If the app only reads port from one place, documenting "start with PORT=5002" or "use 5002 for smoke test" may be enough.
- **Adjustment:** Execute all of P1. For P2, add only the "Automated?" column (traceability); skip the persistence pytest in this pass unless trivial. Revisit P2.5 and P3 in a later iteration.

---

## 5. Critical review (explicit)

- **Is the scope right?** Yes. P1 is documentation + one script change + two extra curl calls. No new frameworks or CI.
- **Value vs effort?** High value (correct port in README, runnable tests doc, better smoke coverage); effort is low.
- **Risks?** App actually runs on 5002 (see `app.run(..., port=5002)` in `app.py`). README and QUICK_START saying 5000 is wrong and can confuse users; fixing to 5002 is a bug fix.
- **Dependencies?** None. test_app.sh only needs curl and python3.
- **Conclusion:** Proceed with P1 and P2 traceability. Do not add persistence pytest in this pass.
