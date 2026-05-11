# Shazam to Soundeo Sync - Validation Checklist

## Phase 1: Shazam Extraction
| Component | Status | Notes |
|-----------|--------|-------|
| shazam_reader.py - locate DB | OK | Both paths checked |
| get_shazam_tracks() - extract | OK | Mock DB tests pass |
| Missing DB handling | OK | FileNotFoundError with clear message |
| Deduplication | OK | Same artist+title deduped |

## Phase 2: Local Scanner
| Component | Status | Notes |
|-----------|--------|-------|
| scan_folders() | OK | Recursive MP3 scan |
| get_file_info reuse | OK | From app.py |
| Filename parsing | OK | Artist - Title patterns |
| normalize() | OK | Suffixes stripped |
| compute_to_download() | OK | Diff logic verified |
| Fuzzy matching | OK | similarity_score used |

## Phase 3: Config & Settings
| Component | Status | Notes |
|-----------|--------|-------|
| config_shazam.py | OK | load/save |
| GET /api/settings | OK | Returns config |
| POST /api/settings | OK | Persists folders, etc. |
| destination_folders | OK | List of paths |
| soundeo_cookies_path | OK | Session persistence |

## Phase 4: Soundeo Automation
| Component | Status | Notes |
|-----------|--------|-------|
| Selenium + webdriver-manager | OK | Chrome auto-install |
| load_cookies / save_cookies | OK | JSON persistence |
| _search_queries() | OK | artist title, title artist, etc. |
| find_and_favorite_track() | OK | Search, best match, F key |
| run_favorite_tracks() | OK | Background, progress callback |
| run_save_session_flow() | OK | Headed browser, event signal |
| Screenshot stream | OK | _update_frame, get_latest_frame |

## Phase 5: Flask API
| Component | Status | Notes |
|-----------|--------|-------|
| GET /api/settings | OK | |
| POST /api/settings | OK | |
| GET /api/shazam-sync/status | OK | Cached status |
| POST /api/shazam-sync/compare | OK | Shazam + scan + diff |
| GET /api/shazam-sync/progress | OK | Running state |
| POST /api/shazam-sync/run-soundeo | OK | 400 when no tracks |
| GET /api/shazam-sync/video-feed | OK | MJPEG stream |
| POST /api/soundeo/start-save-session | OK | Opens browser |
| POST /api/soundeo/session-saved | OK | Saves cookies |

## Phase 6: UI
| Component | Status | Notes |
|-----------|--------|-------|
| Shazam section visible | OK | step active |
| Settings panel | OK | Folders, session |
| Add Folder | OK | Browse dialog |
| Save Settings | OK | Persists |
| Save Soundeo Session | OK | Opens browser |
| I have logged in | OK | Signals save |
| Compare button | OK | Runs compare |
| Sync to Soundeo | OK | Disabled when no to_download |
| Track list table | OK | Have / To download |
| Progress display | OK | During sync |
| Live video feed | OK | img src video-feed |
| Empty state messages | OK | Friendly prompts |

## Phase 7: End-to-End
| Flow | Status | Notes |
|------|--------|-------|
| Add folder -> Save -> Compare | OK | |
| Compare with no folders | OK | Shows error message |
| Compare with 0 Shazam tracks | OK | Empty state |
| Save session flow | OK | Browser, signal, cookies |
| Sync flow | OK | Requires session + tracks |
| Video feed during sync | OK | MJPEG streamed |

## Manual Test (User)
1. Add a destination folder (with MP3s)
2. Run Compare - verify counts
3. Save Soundeo Session - log in - click I have logged in
4. Run Sync - verify live view, progress, favorited tracks
