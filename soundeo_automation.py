"""
Soundeo browser automation via Selenium.
Search tracks, open track page, favorite (F key).
Supports cookie persistence and live screenshot stream.

Important: If a track is already favorited/starred (e.g. button shows blue/active),
we never click the button or press F – that would un-favorite it. We only add
favorites, never remove them.
"""
import os
import io
import json
import time
import threading
import urllib.parse
from typing import List, Dict, Optional, Callable

# Lazy imports - avoid loading Selenium at module level
def _get_driver(headless: bool = False, use_persistent_profile: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    if use_persistent_profile:
        from config_shazam import get_soundeo_browser_profile_dir
        opts.add_argument("--user-data-dir=" + get_soundeo_browser_profile_dir())
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


SOUNDEO_BASE = "https://soundeo.com"
SOUNDEO_LOGIN = f"{SOUNDEO_BASE}/account/logoreg"
TRACK_LIST_URL = f"{SOUNDEO_BASE}/list/tracks"

# Thread-safe holder for latest screenshot (for MJPEG stream)
_frame_lock = threading.Lock()
_latest_frame: Optional[bytes] = None
_streaming_active = False


def _update_frame(driver) -> None:
    """Capture screenshot and store in shared holder."""
    global _latest_frame
    try:
        png = driver.get_screenshot_as_png()
        if png:
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            if img.mode == "RGBA":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            with _frame_lock:
                _latest_frame = buf.getvalue()
    except Exception:
        pass


def get_latest_frame() -> Optional[bytes]:
    """Return latest screenshot bytes for stream (thread-safe)."""
    with _frame_lock:
        return _latest_frame


def set_streaming_active(active: bool) -> None:
    global _streaming_active
    _streaming_active = active


def is_streaming_active() -> bool:
    return _streaming_active


def _cookie_for_selenium(c: dict, current_domain: str) -> dict:
    """Build a cookie dict that Selenium add_cookie accepts. Preserve sameSite and domain (with leading dot) so session cookies work."""
    domain = (c.get("domain") or current_domain).strip()
    # Keep leading dot if present (e.g. .soundeo.com) so cookie scope matches what the site set
    out = {
        "name": c.get("name"),
        "value": c.get("value"),
        "domain": domain or current_domain,
        "path": c.get("path") or "/",
    }
    if c.get("secure"):
        out["secure"] = True
    if c.get("httpOnly") is not None:
        out["httpOnly"] = bool(c["httpOnly"])
    if c.get("expiry") is not None:
        try:
            out["expiry"] = int(c["expiry"])
        except (TypeError, ValueError):
            pass
    # Selenium requires sameSite to be one of 'Strict', 'Lax', 'None' (capitalized). Session cookies often use None.
    ss = (c.get("sameSite") or "").strip()
    if ss and ss.lower() in ("strict", "lax", "none"):
        out["sameSite"] = ss.capitalize() if ss.lower() != "none" else "None"
    return out


def load_cookies(driver, cookies_path: str) -> bool:
    """Load saved cookies. Same domain flow as save: hit site, add cookies, then go to base so session applies."""
    cookies_path = os.path.abspath(cookies_path)
    # #region agent log
    try:
        _log = {"location": "soundeo_automation.py:load_cookies", "message": "load_cookies entry", "data": {"path": cookies_path, "file_exists": os.path.exists(cookies_path)}, "timestamp": int(time.time() * 1000), "hypothesisId": "H1H4H5"}
        with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_log) + "\n")
    except Exception:
        pass
    # #endregion
    if not os.path.exists(cookies_path):
        return False
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies or not isinstance(cookies, list):
            return False
        # Same first URL as save flow so domain/path match
        driver.get(SOUNDEO_LOGIN)
        time.sleep(1.5)
        parsed = urllib.parse.urlparse(SOUNDEO_BASE)
        current_domain = parsed.netloc or "soundeo.com"
        added = 0
        for c in cookies:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            try:
                safe = _cookie_for_selenium(c, current_domain)
                driver.add_cookie(safe)
                added += 1
            except Exception:
                try:
                    driver.add_cookie({
                        "name": c["name"],
                        "value": c.get("value", ""),
                        "domain": current_domain,
                        "path": c.get("path") or "/",
                    })
                    added += 1
                except Exception:
                    pass
        # #region agent log
        try:
            _log = {"location": "soundeo_automation.py:load_cookies", "message": "load_cookies result", "data": {"path": cookies_path, "cookies_in_file": len(cookies), "added": added}, "timestamp": int(time.time() * 1000), "hypothesisId": "H3H4"}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_log) + "\n")
        except Exception:
            pass
        # #endregion
        if added == 0:
            return False
        # Navigate to main site so the session is applied (avoids login modal)
        driver.get(SOUNDEO_BASE)
        time.sleep(2.5)
        return True
    except Exception:
        return False


def save_cookies(driver, cookies_path: str) -> bool:
    """Save current cookies to file. Uses absolute path so save and load use the same file."""
    try:
        cookies_path = os.path.abspath(cookies_path)
        cookies = driver.get_cookies()
        # #region agent log
        try:
            _log = {"location": "soundeo_automation.py:save_cookies", "message": "save_cookies before write", "data": {"path": cookies_path, "cookie_count": len(cookies) if cookies else 0}, "timestamp": int(time.time() * 1000), "hypothesisId": "H2H4"}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_log) + "\n")
        except Exception:
            pass
        # #endregion
        if not cookies:
            return False
        os.makedirs(os.path.dirname(cookies_path) or ".", exist_ok=True)
        with open(cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        # #region agent log
        try:
            _sz = os.path.getsize(cookies_path) if os.path.exists(cookies_path) else 0
            _log = {"location": "soundeo_automation.py:save_cookies", "message": "save_cookies after write", "data": {"path": cookies_path, "file_size": _sz}, "timestamp": int(time.time() * 1000), "hypothesisId": "H4"}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_log) + "\n")
        except Exception:
            pass
        # #endregion
        return True
    except Exception:
        return False


def _search_queries(artist: str, title: str) -> List[str]:
    """Generate search query variants."""
    a, t = (artist or "").strip(), (title or "").strip()
    queries = []
    if a and t:
        queries.extend([f"{a} {t}", f"{t} {a}", a, t])
    elif a:
        queries.append(a)
    elif t:
        queries.append(t)
    return [q for q in queries if q]


def _best_match_score(track: Dict, link_text: str, target_artist: str, target_title: str) -> float:
    """Score a result link against target artist/title. Higher = better match."""
    from app import similarity_score
    text_lower = (link_text or "").lower()
    # Often "Artist - Title (Mix)"
    artist_score = similarity_score(target_artist, text_lower) if target_artist else 0.5
    title_score = similarity_score(target_title, text_lower) if target_title else 0.5
    return artist_score * 0.4 + title_score * 0.6


def find_and_favorite_track(
    driver,
    artist: str,
    title: str,
    on_progress: Optional[Callable[[str], None]] = None,
    update_frame_fn: Optional[Callable] = None,
    delay: float = 2.5,
) -> Optional[str]:
    """
    Search for track on Soundeo, open best match, press F to favorite.
    Returns track URL if successful, else None.
    """
    queries = _search_queries(artist, title)
    for q in queries:
        encoded = urllib.parse.quote(q)
        url = f"{TRACK_LIST_URL}?searchFilter={encoded}&availableFilter=1"
        if on_progress:
            on_progress(f"Searching: {q}")
        driver.get(url)
        time.sleep(1.5)
        if update_frame_fn:
            update_frame_fn()

        # Find track links: a[href*="/track/"]
        from selenium.webdriver.common.by import By
        try:
            links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
        except Exception:
            links = []

        if not links:
            continue

        best_link = None
        best_score = -1
        for lnk in links[:15]:
            try:
                txt = (lnk.text or "").strip()
                if not txt or len(txt) < 3:
                    continue
                score = _best_match_score({}, txt, artist, title)
                if score > best_score:
                    best_score = score
                    best_link = lnk
            except Exception:
                pass

        if best_link and best_score >= 0.3:
            try:
                href = best_link.get_attribute("href")
                if not href:
                    continue
                if on_progress:
                    on_progress(f"Opening: {best_link.text[:50]}...")
                driver.get(href)
                time.sleep(2)
                if update_frame_fn:
                    update_frame_fn()

                # Click favorite button only if not already favorited (blue/starred). Never un-favorite.
                favorited = False
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

                def _is_already_favorited(btn) -> bool:
                    """True if button/element indicates already favorited (e.g. blue/active state). Do not click then."""
                    if not btn:
                        return False
                    try:
                        cls = (btn.get_attribute("class") or "").lower()
                        if any(x in cls for x in ("active", "favorited", "starred", "selected", "on", "added")):
                            return True
                        if (btn.get_attribute("aria-pressed") or "").lower() == "true":
                            return True
                        if (btn.get_attribute("data-favorited") or btn.get_attribute("data-active") or "").lower() in ("true", "1"):
                            return True
                        # Blue color often means favorited: check computed color (blue-ish)
                        try:
                            color = driver.execute_script(
                                "var s=window.getComputedStyle(arguments[0]); return s.fill||s.color||'';", btn
                            )
                            if color and ("rgb(0, 0, 255)" in color or "blue" in color.lower() or "rgb(59," in color or "rgb(37," in color):
                                return True
                        except Exception:
                            pass
                    except Exception:
                        pass
                    return False

                for selector in (
                    "button.favorites",
                    "button.favorite",
                    "button[class*='favorite']",
                    "button[data-track-id]",
                ):
                    try:
                        btn = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                        if btn and btn.is_displayed():
                            if _is_already_favorited(btn):
                                favorited = True  # Already favorited; do not click (would un-favorite)
                                time.sleep(0.3)
                                break
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                            time.sleep(0.3)
                            btn.click()
                            favorited = True
                            time.sleep(0.8)
                            break
                    except Exception:
                        pass
                if not favorited:
                    # Fallback: press F key (only if we didn't skip due to already favorited)
                    from selenium.webdriver.common.keys import Keys
                    from selenium.webdriver.common.action_chains import ActionChains
                    try:
                        body = driver.find_element(By.TAG_NAME, "body")
                        ActionChains(driver).send_keys(body, "f").perform()
                        favorited = True
                        time.sleep(0.8)
                    except Exception:
                        pass
                if update_frame_fn:
                    update_frame_fn()

                return href
            except Exception:
                pass

        time.sleep(delay)

    return None


# Stop requested from UI (checked between tracks)
_sync_stop_requested = False


def request_sync_stop() -> None:
    """Request the running sync to stop after the current track."""
    global _sync_stop_requested
    _sync_stop_requested = True


def clear_sync_stop_request() -> None:
    global _sync_stop_requested
    _sync_stop_requested = False


# Event for save-session flow: set when user has logged in
_save_session_done: Optional[threading.Event] = None
# Set by run_save_session_flow if browser fails to start (e.g. profile in use); read by API after short wait
_save_session_last_error: Optional[str] = None


def run_save_session_flow(
    cookies_path: str,
    headed: bool = True,
    done_event: Optional[threading.Event] = None,
) -> Dict:
    """
    Open browser for user to log in (or show existing session), then save cookies when done_event is set.
    If a saved session file exists, we load it first so the browser opens already logged in.
    After user signals done, we navigate to base and wait so post-login redirect/cookies settle, then save.
    """
    global _save_session_last_error
    _save_session_last_error = None
    driver = None
    try:
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
    except Exception as e:
        # Profile may be in use (e.g. another Chrome window). Retry with temp profile so browser still opens.
        try:
            driver = _get_driver(headless=not headed, use_persistent_profile=False)
        except Exception as e2:
            _save_session_last_error = str(e2)
            return {"success": False, "message": "Browser failed to start", "error": str(e2)}
    try:
        cookies_path = os.path.abspath(cookies_path)
        # #region agent log
        try:
            _log = {"location": "soundeo_automation.py:run_save_session_flow", "message": "save_session path", "data": {"cookies_path": cookies_path}, "timestamp": int(time.time() * 1000), "hypothesisId": "H1H5"}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_log) + "\n")
        except Exception:
            pass
        # #endregion
        had_session = load_cookies(driver, cookies_path)
        if had_session:
            # Already loaded saved cookies; stay on SOUNDEO_BASE so user sees logged-in state
            driver.get(SOUNDEO_BASE)
            time.sleep(1)
        else:
            # No saved session: show login page
            driver.get(SOUNDEO_LOGIN)
        if done_event is not None:
            done_event.wait(timeout=300)  # 5 min max wait
        else:
            input("Log in to Soundeo in the browser, then press Enter here to save session...")
        # Let post-login redirect complete and session cookies be set, then save
        driver.get(SOUNDEO_BASE)
        time.sleep(3)
        ok = save_cookies(driver, cookies_path)
        # #region agent log
        try:
            _log = {"location": "soundeo_automation.py:run_save_session_flow", "message": "save_session save_cookies result", "data": {"path": cookies_path, "save_ok": ok}, "timestamp": int(time.time() * 1000), "hypothesisId": "H2H4"}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_log) + "\n")
        except Exception:
            pass
        # #endregion
        return {"success": ok, "message": "Session saved" if ok else "Failed to save cookies"}
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def create_save_session_event() -> threading.Event:
    """Create and return event for save-session flow. Call signal_save_session_done when user has logged in."""
    global _save_session_done
    _save_session_done = threading.Event()
    return _save_session_done


def signal_save_session_done() -> None:
    """Signal that user has completed login (for save-session flow)."""
    if _save_session_done:
        _save_session_done.set()


def run_favorite_tracks(
    tracks: List[Dict[str, str]],
    cookies_path: str,
    headed: bool = False,
    stream_frames: bool = True,
    on_progress: Optional[Callable[[int, int, str, Optional[str]], None]] = None,
) -> Dict:
    """
    For each track: search, open, favorite.
    Run in background thread; returns results via on_progress.
    Stops if request_sync_stop() was called (check between tracks).
    """
    global _sync_stop_requested
    clear_sync_stop_request()
    set_streaming_active(stream_frames)
    driver = _get_driver(headless=not headed)
    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "stopped": False}
    update_fn = lambda: _update_frame(driver) if stream_frames else None

    try:
        driver.get(SOUNDEO_BASE)
        time.sleep(2)
        if not load_cookies(driver, cookies_path):
            set_streaming_active(False)
            return {"error": "No saved session. Save Soundeo session first in Settings."}

        if update_fn:
            update_fn()

        for i, t in enumerate(tracks):
            if _sync_stop_requested:
                results["stopped"] = True
                if on_progress:
                    on_progress(i, len(tracks), "Stopped by user", None)
                break
            artist = t.get("artist", "")
            title = t.get("title", "")
            progress_msg = f"Processing {i+1}/{len(tracks)}: {artist} - {title}"

            def prog(s):
                if on_progress:
                    on_progress(i + 1, len(tracks), s, None)

            url = find_and_favorite_track(
                driver, artist, title,
                on_progress=prog, update_frame_fn=update_fn, delay=2.5
            )
            if url:
                results["done"] += 1
                results["urls"][f"{artist} - {title}"] = url
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Favorited: {artist} - {title}", url)
            else:
                results["failed"] += 1
                results["errors"].append(f"Not found: {artist} - {title}")
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Not found: {artist} - {title}", None)

            time.sleep(2.5)

    except Exception as e:
        results["error"] = str(e)
    finally:
        driver.quit()
        set_streaming_active(False)

    return results
