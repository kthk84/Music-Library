"""
Soundeo browser automation via Selenium.
Single driver path: either attach to running Chrome or launch Chrome with a persistent profile.
Config via config_shazam.get_soundeo_browser_config() (mode "attach" | "launch").
"""
import os
import re
import logging
import json
import time
import threading
import urllib.parse
from typing import List, Dict, Optional, Callable, Tuple, Union

_ONE_YEAR = 365 * 24 * 60 * 60


# Lock/marker files Chrome leaves behind when killed; removing them reduces "Something went wrong when opening your profile".
_PROFILE_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _clean_profile_dir(profile_path: str, profile_subdir: Optional[str] = None) -> None:
    """Remove stale lock files from Chrome user-data-dir (and optional profile subdir) so launches don't show profile error dialog."""
    for dir_path in (profile_path,):
        if not dir_path or not os.path.isdir(dir_path):
            continue
        for name in _PROFILE_LOCK_FILES:
            p = os.path.join(dir_path, name)
            try:
                os.remove(p)
            except OSError:
                pass
    if profile_subdir:
        sub_path = os.path.join(profile_path, profile_subdir)
        if os.path.isdir(sub_path):
            for name in _PROFILE_LOCK_FILES:
                p = os.path.join(sub_path, name)
                try:
                    os.remove(p)
                except OSError:
                    pass


def _reset_profile_dir(profile_path: str) -> None:
    """Delete and recreate a corrupted Chrome profile directory."""
    import shutil
    if profile_path and os.path.isdir(profile_path):
        shutil.rmtree(profile_path, ignore_errors=True)
    os.makedirs(profile_path, exist_ok=True)


def _get_driver(headless: bool = False, use_persistent_profile: bool = True):
    """
    Get Chrome WebDriver from config. Two modes:
    - attach: connect to Chrome already running with --remote-debugging-port=9222 (do not quit driver).
    - launch: start Chrome with user-data-dir + optional profile-directory (persistent login).
    If the profile is corrupted, automatically resets it and retries once.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    from config_shazam import get_soundeo_browser_config

    opts = Options()
    cfg = get_soundeo_browser_config()

    if cfg.get("mode") == "attach":
        addr = cfg.get("debugger_address", "127.0.0.1:9222")
        if ":" not in str(addr):
            addr = "127.0.0.1:" + str(addr).strip()
        opts.add_experimental_option("debuggerAddress", addr)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver._connected_to_existing = True
        return driver

    user_data_dir = cfg.get("user_data_dir", "")

    profile_directory = cfg.get("profile_directory")

    def _build_opts():
        o = Options()
        if use_persistent_profile and user_data_dir:
            o.add_argument("--user-data-dir=" + user_data_dir)
            if profile_directory:
                o.add_argument("--profile-directory=" + profile_directory)
        if headless:
            o.add_argument("--headless=new")
        o.add_argument("--window-size=1280,900")
        o.add_argument("--disable-gpu")
        o.add_argument("--no-sandbox")
        # Suppress "Something went wrong when opening your profile" when profile was previously killed
        o.add_argument("--noerrdialogs")
        o.add_argument("--no-first-run")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        o.add_argument("--disable-blink-features=AutomationControlled")
        return o

    _clean_profile_dir(user_data_dir, profile_directory)

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=_build_opts())
        driver._connected_to_existing = False
        return driver
    except Exception as first_err:
        if not use_persistent_profile or not user_data_dir:
            raise
        _reset_profile_dir(user_data_dir)
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=_build_opts())
            driver._connected_to_existing = False
            return driver
        except Exception:
            raise first_err


SOUNDEO_BASE = "https://soundeo.com"
SOUNDEO_LOGIN = f"{SOUNDEO_BASE}/account/logoreg"
SOUNDEO_ACCOUNT = f"{SOUNDEO_BASE}/account"  # If user can view this page, they are logged in
TRACK_LIST_URL = f"{SOUNDEO_BASE}/list/tracks"
FAVORITES_URL = f"{SOUNDEO_BASE}/account/favorites"


def _ensure_expiry(c: dict) -> dict:
    """If cookie has no expiry (session cookie), stamp it 1 year out so it survives browser restarts."""
    if c.get("expiry"):
        return c
    c = dict(c)
    c["expiry"] = int(time.time()) + _ONE_YEAR
    return c


def _dedup_cookies(cookies: list) -> list:
    """Keep only one cookie per (name, path) preferring .soundeo.com domain (widest scope)."""
    best = {}
    for c in cookies:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        key = (c["name"], c.get("path") or "/")
        existing = best.get(key)
        if existing is None:
            best[key] = c
        else:
            dom_new = (c.get("domain") or "").strip()
            dom_old = (existing.get("domain") or "").strip()
            if dom_new.startswith(".") and not dom_old.startswith("."):
                best[key] = c
    return list(best.values())


def _cookie_for_selenium(c: dict, current_domain: str) -> dict:
    """Build a cookie dict that Selenium add_cookie accepts."""
    c = _ensure_expiry(c)
    domain = (c.get("domain") or current_domain).strip()
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
    ss = (c.get("sameSite") or "").strip()
    if ss and ss.lower() in ("strict", "lax", "none"):
        out["sameSite"] = ss.capitalize() if ss.lower() != "none" else "None"
    return out


def _get_all_cookies_cdp(driver) -> list:
    """Use Chrome DevTools Protocol to get ALL cookies (more reliable than driver.get_cookies)."""
    try:
        result = driver.execute_cdp_cmd("Network.getAllCookies", {})
        raw = result.get("cookies", [])
        out = []
        for c in raw:
            domain = (c.get("domain") or "")
            if "soundeo" not in domain:
                continue
            sc = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": domain,
                "path": c.get("path", "/"),
            }
            if c.get("secure"):
                sc["secure"] = True
            if c.get("httpOnly"):
                sc["httpOnly"] = True
            if c.get("expires") and c["expires"] > 0:
                sc["expiry"] = int(c["expires"])
            ss = c.get("sameSite", "")
            if ss and ss.lower() in ("strict", "lax", "none"):
                sc["sameSite"] = ss.capitalize() if ss.lower() != "none" else "None"
            out.append(sc)
        return out
    except Exception:
        return []


def load_cookies(driver, cookies_path: str) -> bool:
    """
    Load saved cookies into the browser.
    Navigates to the base site first (not login page) to establish the domain,
    then injects cookies with guaranteed expiry so they persist.
    """
    cookies_path = os.path.abspath(cookies_path)
    if not os.path.exists(cookies_path):
        return False
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies or not isinstance(cookies, list):
            return False
        cookies = _dedup_cookies(cookies)
        driver.get(SOUNDEO_BASE)
        time.sleep(2)
        current_domain = "soundeo.com"
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
                    fallback = _ensure_expiry(c)
                    driver.add_cookie({
                        "name": fallback["name"],
                        "value": fallback.get("value", ""),
                        "domain": current_domain,
                        "path": fallback.get("path") or "/",
                        "expiry": fallback.get("expiry", int(time.time()) + _ONE_YEAR),
                    })
                    added += 1
                except Exception:
                    pass
        if added == 0:
            return False
        return True
    except Exception:
        return False


def _is_redirected_to_login(driver) -> bool:
    """True if current page is a login/redirect (logoreg, /login, /account/log)."""
    try:
        current = (driver.current_url or "").strip().lower()
        return "logoreg" in current or "/login" in current or "/account/log" in current
    except Exception:
        return True


def verify_logged_in(driver) -> bool:
    """
    Check that the current session is actually logged in to Soundeo.
    Navigate to https://soundeo.com/account — if the user can view that page (not redirected to login),
    they are logged in. Call this after load_cookies() and before any action that reads or changes favorites.
    """
    try:
        driver.get(SOUNDEO_ACCOUNT)
        time.sleep(1.5)
        current = (driver.current_url or "").strip().lower()
        # Logged-out users are typically redirected to login (logoreg, login, etc.)
        if "logoreg" in current or "/login" in current or "/account/log" in current:
            return False
        # Still on /account or a subpath (e.g. /account/favorites) = logged in
        if "/account" in current and "log" not in current:
            return True
        # Fallback: check for login prompt in body
        try:
            from selenium.webdriver.common.by import By
            body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
            if body and ("log in" in body or "sign in" in body) and "account" not in body[:800]:
                return False
        except Exception:
            pass
        return "/account" in current
    except Exception:
        return False


def save_cookies(driver, cookies_path: str) -> bool:
    """
    Save current Soundeo cookies to file.
    Uses CDP for complete capture and stamps session cookies with a far-future
    expiry so they survive browser restarts.
    """
    try:
        cookies_path = os.path.abspath(cookies_path)
        cookies = _get_all_cookies_cdp(driver)
        if not cookies:
            cookies = driver.get_cookies()
        if not cookies:
            return False
        cookies = _dedup_cookies([_ensure_expiry(c) for c in cookies])
        os.makedirs(os.path.dirname(cookies_path) or ".", exist_ok=True)
        with open(cookies_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        return True
    except Exception:
        return False


def _graceful_quit(driver) -> None:
    """
    Flush cookies to the profile before killing Chrome.
    Navigates to about:blank (lets pending writes finish), waits, then quits.
    """
    if driver is None:
        return
    if getattr(driver, "_connected_to_existing", False):
        return
    try:
        driver.get("about:blank")
        time.sleep(1)
    except Exception:
        pass
    try:
        driver.quit()
    except Exception:
        pass


def _artist_tokens_for_search(artist: str) -> List[str]:
    """Split artist on & , feat. etc, strip parens, return sorted tokens (min 2 chars).
    Used to build search queries that match Soundeo's indexing (e.g. Khainz, Mariz, KASIA)."""
    if not artist or not isinstance(artist, str):
        return []
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', artist)  # strip (ofc), (feat. X) etc
    tokens = re.split(r"\s*[&,]\s*|\s+feat\.?\s+", s, flags=re.I)
    tokens = [t.strip() for t in tokens if t and len(t.strip()) >= 2]
    return sorted(set(tokens))


def _search_queries(artist: str, title: str) -> List[str]:
    """Generate search query variants.

    Only combined artist+title queries; partial (artist-only / title-only)
    queries cause too many false matches (e.g. matching a random popular
    track that shares one word).

    Adds artist-order-normalized variants so "KASIA, Khainz & Mariz" can
    match Soundeo results like "Khainz, Mariz, KASIA (ofc) - Stop Go".
    Adds dot-stripped artist variant so "R.E.Zarin" is also tried as
    "REZarin" (Soundeo search works with rezarin+androme).
    """
    a, t = (artist or "").strip(), (title or "").strip()
    seen = set()
    queries = []

    def _add(q: str) -> None:
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if a and t:
        # Try dot-stripped artist first (e.g. "REZarin Androme") — Soundeo search often matches better without dots
        a_no_dots = a.replace(".", "")
        if a_no_dots != a:
            _add(f"{a_no_dots} {t}")
            _add(f"{t} {a_no_dots}")
        _add(f"{a} {t}")
        _add(f"{t} {a}")
        tokens = _artist_tokens_for_search(a)
        if tokens:
            norm_artist = " ".join(tokens)
            if norm_artist.lower() != a.lower():
                _add(f"{norm_artist} {t}")
                _add(f"{t} {norm_artist}")
        if len(t.split()) >= 3:
            _add(t)
        # Core title (strip all parens) often matches Soundeo display/slug better
        core_title = _strip_all_parens(t)
        if core_title and core_title.lower() != t.lower():
            _add(f"{a} {core_title}")
            _add(f"{core_title} {a}")
    elif a:
        _add(a)
    elif t:
        _add(t)
    return queries


def _strip_parens_suffix(s: str) -> str:
    """Remove trailing (Mix), (ofc), (feat. X) etc for cleaner matching."""
    if not s or not isinstance(s, str):
        return s
    return re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()


def _strip_all_parens(s: str) -> str:
    """Remove every (...) and [...] from the string and collapse spaces. For title normalization."""
    if not s or not isinstance(s, str):
        return s
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _best_match_score(track: Dict, link_text: str, target_artist: str, target_title: str) -> float:
    """Score a result link against target artist/title. Higher = better match.

    Soundeo results are typically formatted as "ARTIST - Title (Mix)".
    We split the result text on " - " and compare each part against the
    target artist and title separately. Both parts must score above a
    minimum for the match to be accepted (returns 0 otherwise).
    Parenthetical suffixes like (ofc), (Original Mix) are stripped for matching.
    """
    from app import similarity_score
    text = (link_text or "").strip()
    if not text:
        return 0.0

    t_artist = (target_artist or "").strip()
    t_title = (target_title or "").strip()

    if not t_artist and not t_title:
        return 0.0

    text_lower = text.lower()

    if " - " in text:
        r_artist, r_title = text.split(" - ", 1)
        r_artist = _strip_parens_suffix(r_artist)
        r_title = _strip_parens_suffix(r_title)
    else:
        r_artist = ""
        r_title = _strip_parens_suffix(text)

    artist_score = similarity_score(t_artist, r_artist) if t_artist and r_artist else 0.0
    # Normalize dots so "R.E.Zarin" matches "REZarin" (Soundeo result)
    if t_artist and r_artist and artist_score < 0.9:
        t_artist_nd = t_artist.replace(".", "").lower()
        r_artist_nd = r_artist.replace(".", "").lower()
        if t_artist_nd == r_artist_nd or similarity_score(t_artist_nd, r_artist_nd) >= 0.9:
            artist_score = max(artist_score, 0.95)
    title_score = similarity_score(t_title, r_title) if t_title else 0.0
    # Also compare normalized titles (strip all parens) so "You Got Worked (feat. X) (Stripped Remix)" matches "You Got Worked (Stripped Remix)"
    t_core = _strip_all_parens(t_title) if t_title else ""
    r_core = _strip_all_parens(r_title) if r_title else ""
    if t_core and r_core:
        title_score = max(title_score, similarity_score(t_core, r_core))
    # If target core title's words all appear in result text, treat as strong match (result often has extra "feat. X" etc.)
    if t_core:
        def _norm(w: str) -> str:
            w = (w or "").strip()
            while w and w[-1] in ".,;:&()[]":
                w = w[:-1].strip()
            while w and w[0] in ".,;:&()[]":
                w = w[1:].strip()
            return w
        t_words = set(_norm(w) for w in t_core.split() if _norm(w))
        if t_words:
            result_text = (r_core or text_lower)
            result_words = set(_norm(w) for w in result_text.split() if _norm(w))
            if t_words <= result_words:
                title_score = max(title_score, 0.75)

    if not t_artist:
        artist_score = title_score * 0.3

    if t_artist and t_title:
        if artist_score < 0.15 or title_score < 0.15:
            return 0.0

    whole_artist = similarity_score(t_artist, text_lower) if t_artist else 0.0
    whole_title = similarity_score(t_title, text_lower) if t_title else 0.0
    if t_title:
        t_core = _strip_all_parens(t_title)
        if t_core:
            whole_title = max(whole_title, similarity_score(t_core, text_lower))
    split_score = artist_score * 0.4 + title_score * 0.6
    whole_score = whole_artist * 0.4 + whole_title * 0.6

    return max(split_score, whole_score)


_MATCH_THRESHOLD = 0.55

def _extended_preference_bonus(link_text: str) -> float:
    """
    Prefer Extended Mix/Version when multiple links match (explicit rule).
    Small bonus so it only differentiates between otherwise-equal matches.
    """
    if not link_text:
        return 0.0
    if "extended" in link_text.lower():
        return 0.1
    return 0.0


def find_track_on_soundeo(
    driver,
    artist: str,
    title: str,
    on_progress: Optional[Callable[[str], None]] = None,
    delay: float = 2.5,
) -> Optional[tuple]:
    """
    Search for track on Soundeo and return (url, display_text, score) of the best match.
    Does NOT open the track page or click favorite — use for "Search on Soundeo" only.
    Evaluates ALL query variants and returns the single best match across all.
    """
    queries = _search_queries(artist, title)
    overall_best_link = None
    overall_best_score = -1
    overall_best_href = None
    overall_best_text = None

    from selenium.webdriver.common.by import By

    first_get = True
    for q in queries:
        try:
            encoded = urllib.parse.quote(q)
            if on_progress:
                on_progress(f"Searching: {q}")
            url = f"{TRACK_LIST_URL}?searchFilter={encoded}&availableFilter=1"
            driver.get(url)
            time.sleep(1.5)
            if first_get:
                first_get = False
                if _is_redirected_to_login(driver):
                    return None
            try:
                links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
            except Exception:
                links = []
            if not links:
                driver.get(f"{SOUNDEO_BASE}/search?q={encoded}")
                time.sleep(1.5)
                try:
                    links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
                except Exception:
                    links = []
            if not links:
                time.sleep(delay)
                continue

            for lnk in links[:15]:
                try:
                    txt = (lnk.text or "").strip()
                    if not txt or len(txt) < 3:
                        continue
                    score = _best_match_score({}, txt, artist, title) + _extended_preference_bonus(txt)
                    if score > overall_best_score:
                        overall_best_score = score
                        overall_best_link = lnk
                        overall_best_text = txt
                        try:
                            overall_best_href = lnk.get_attribute("href")
                        except Exception:
                            overall_best_href = None
                except Exception:
                    pass

            time.sleep(delay)
        except Exception as e:
            logging.warning("find_track_on_soundeo: query %r failed, trying next: %s", q, e)
            time.sleep(delay)

    if overall_best_href and overall_best_score >= _MATCH_THRESHOLD:
        return (overall_best_href, overall_best_text or "", overall_best_score)

    return None


def find_and_favorite_track(
    driver,
    artist: str,
    title: str,
    on_progress: Optional[Callable[[str], None]] = None,
    delay: float = 2.5,
    already_starred: Optional[set] = None,
) -> Optional[tuple]:
    """
    Search for track on Soundeo, open best match, favorite if not already starred.
    If already_starred contains "Artist - Title", we never open the track page or click
    anything (to avoid ever un-starring). Returns (url, display_text, score) or None.
    Evaluates ALL query variants and picks the single best match.
    """
    queries = _search_queries(artist, title)
    overall_best_link = None
    overall_best_score = -1
    overall_best_href = None
    overall_best_text = None

    from selenium.webdriver.common.by import By

    first_get = True
    for q in queries:
        try:
            encoded = urllib.parse.quote(q)
            if on_progress:
                on_progress(f"Searching: {q}")
            url = f"{TRACK_LIST_URL}?searchFilter={encoded}&availableFilter=1"
            driver.get(url)
            time.sleep(1.5)
            if first_get:
                first_get = False
                if _is_redirected_to_login(driver):
                    return None
            try:
                links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
            except Exception:
                links = []
            if not links:
                driver.get(f"{SOUNDEO_BASE}/search?q={encoded}")
                time.sleep(1.5)
                try:
                    links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
                except Exception:
                    links = []
            if not links:
                time.sleep(delay)
                continue

            for lnk in links[:15]:
                try:
                    txt = (lnk.text or "").strip()
                    if not txt or len(txt) < 3:
                        continue
                    score = _best_match_score({}, txt, artist, title) + _extended_preference_bonus(txt)
                    if score > overall_best_score:
                        overall_best_score = score
                        overall_best_link = lnk
                        overall_best_text = txt
                        try:
                            overall_best_href = lnk.get_attribute("href")
                        except Exception:
                            overall_best_href = None
                except Exception:
                    pass

            time.sleep(delay)
        except Exception as e:
            logging.warning("find_and_favorite_track: query %r failed, trying next: %s", q, e)
            time.sleep(delay)

    if overall_best_link and overall_best_href and overall_best_score >= _MATCH_THRESHOLD:
        href = overall_best_href
        display_text = overall_best_text or ""
        key = f"{artist} - {title}"
        if already_starred:
            dn = _strip_all_parens_lower(key).replace(' & ', ', ')
            if ' - ' in dn:
                ap, tp = dn.split(' - ', 1)
                dn = ', '.join(sorted(a.strip() for a in ap.split(', ') if a.strip())) + ' - ' + tp
            if key in already_starred or key.lower() in already_starred or dn in already_starred:
                if on_progress:
                    on_progress(f"Already starred: {key[:50]}...")
                return (href, display_text, overall_best_score)

        try:
            if on_progress:
                on_progress(f"Opening: {display_text[:50]}...")
            driver.get(href)
            time.sleep(2)

            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            favorited = False
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
                        if _is_favorited_state(driver, btn):
                            favorited = True
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
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.common.action_chains import ActionChains
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    ActionChains(driver).send_keys(body, "f").perform()
                    favorited = True
                    time.sleep(0.8)
                except Exception:
                    pass

            return (href, display_text, overall_best_score)
        except Exception:
            pass

    return None


def _is_favorited_state(driver, btn) -> bool:
    """True if button/element indicates already favorited. On Soundeo: blue = starred, grey = not starred."""
    if not btn:
        return False
    try:
        # Soundeo: starred = blue, not starred = grey. Use color as primary signal.
        try:
            color_result = driver.execute_script("""
                var el = arguments[0];
                var svg = el.tagName === 'svg' ? el : (el.querySelector && el.querySelector('svg'));
                var node = (svg && svg.querySelector('path')) ? svg.querySelector('path') : el;
                var s = window.getComputedStyle(node);
                var c = (s.fill || s.color || '').trim();
                if (!c || c === 'none' || c === 'transparent') c = (el.fill || el.color || '').trim() || (s.fill || s.color);
                if (!c) return 'unknown';
                var m = c.match(/rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
                if (!m) return c.indexOf('blue') !== -1 ? 'blue' : 'unknown';
                var r = parseInt(m[1],10), g = parseInt(m[2],10), b = parseInt(m[3],10);
                if (b > r && b > g && r < 180) return 'blue';
                if (Math.abs(r-g) < 40 && Math.abs(g-b) < 40 && Math.abs(r-b) < 40) return 'grey';
                return 'unknown';
            """, btn)
            if color_result == "blue":
                return True
            if color_result == "grey":
                return False
        except Exception:
            pass
        cls = (btn.get_attribute("class") or "").lower()
        words = set(re.split(r"[^a-z0-9]+", cls))
        if words & {"active", "favorited", "starred", "selected", "on", "added", "filled"}:
            return True
        if (btn.get_attribute("aria-pressed") or "").lower() == "true":
            return True
        if (btn.get_attribute("data-favorited") or btn.get_attribute("data-active") or "").lower() in ("true", "1"):
            return True
        try:
            color = driver.execute_script(
                "var s=window.getComputedStyle(arguments[0]); return s.fill||s.color||'';", btn
            )
            if color and ("rgb(0, 0, 255)" in color or "blue" in color.lower() or "rgb(59," in color or "rgb(37," in color):
                return True
        except Exception:
            pass
        try:
            has_fill = driver.execute_script("""
                var el = arguments[0];
                var svg = el.tagName === 'svg' ? el : (el.querySelector && el.querySelector('svg'));
                if (!svg) return false;
                var path = svg.querySelector('path');
                if (!path) return false;
                var fill = (path.getAttribute('fill') || window.getComputedStyle(path).fill || '').toLowerCase();
                return !!(fill && fill !== 'none' && fill !== 'transparent');
            """, btn)
            if has_fill:
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def get_track_starred_state(driver, track_url: str) -> bool:
    """
    Open a Soundeo track URL and return True if the track is currently favorited/starred (read-only, no click).
    Caller must have loaded cookies. Use when we already have the track URL and only need star state.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        return False
    try:
        driver.get(track_url.strip())
        # Wait for favorite control to appear (Soundeo may load it dynamically)
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='favorite'], [class*='Favorite'], button[data-track-id], [aria-label*='avorite']"))
            )
        except Exception:
            pass
        time.sleep(1.5)
        # On detail page there is one star for this track; player bar may have another (other track). Prefer main-content star only.
        try:
            is_starred_js = driver.execute_script("""
                var sel = "[class*='favorite'], [class*='Favorite'], button[data-track-id], [aria-label*='avorite'], [title*='avorite']";
                var nodes = document.querySelectorAll(sel);
                function isInPlayer(el) {
                    var p = el;
                    while (p) {
                        var c = (p.className || '') + ' ' + (p.id || '');
                        if (/player|playbar|now-playing|mini-player|fixed.*bottom|bottom.*bar/i.test(c)) return true;
                        var s = window.getComputedStyle(p);
                        if (s.position === 'fixed' && (s.bottom === '0px' || parseFloat(s.bottom) < 100)) return true;
                        p = p.parentElement;
                    }
                    return false;
                }
                function isBlueOrGrey(el) {
                    var svg = el.tagName === 'svg' ? el : el.querySelector('svg');
                    var node = (svg && svg.querySelector('path')) ? svg.querySelector('path') : el;
                    var s = window.getComputedStyle(node);
                    var c = (s.fill || s.color || '').trim();
                    if (!c || c === 'none' || c === 'transparent') return null;
                    var m = c.match(/rgba?\\s*\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)/);
                    if (!m) return c.indexOf('blue') !== -1 ? 'blue' : null;
                    var r = parseInt(m[1],10), g = parseInt(m[2],10), b = parseInt(m[3],10);
                    if (b > r && b > g && r < 180) return 'blue';
                    if (Math.abs(r-g) < 40 && Math.abs(g-b) < 40 && Math.abs(r-b) < 40) return 'grey';
                    return null;
                }
                var mainCandidates = [], playerCandidates = [];
                for (var i = 0; i < nodes.length; i++) {
                    var el = nodes[i];
                    if (!el.offsetParent) continue;
                    if (isInPlayer(el)) { playerCandidates.push(el); continue; }
                    mainCandidates.push(el);
                }
                var toCheck = mainCandidates.length ? mainCandidates : playerCandidates;
                if (toCheck.length === 0) return false;
                var mainStar = toCheck[0];
                for (var j = 0; j < toCheck.length; j++) {
                    if (toCheck[j].getBoundingClientRect().top < mainStar.getBoundingClientRect().top)
                        mainStar = toCheck[j];
                }
                var colorState = isBlueOrGrey(mainStar);
                if (colorState === 'blue') return true;
                if (colorState === 'grey') return false;
                var cls = (mainStar.className || '').toLowerCase();
                if (/\\b(active|favorited|starred|selected|on|added|filled)\\b/.test(cls)) return true;
                if ((mainStar.getAttribute('aria-pressed') || '').toLowerCase() === 'true') return true;
                if ((mainStar.getAttribute('data-favorited') || mainStar.getAttribute('data-active') || '').toLowerCase().match(/true|1/)) return true;
                var svg = mainStar.tagName === 'svg' ? mainStar : mainStar.querySelector('svg');
                if (svg) {
                    var path = svg.querySelector('path');
                    if (path) {
                        var fill = (path.getAttribute('fill') || getComputedStyle(path).fill || '').toLowerCase();
                        if (fill && fill !== 'none' && fill !== 'transparent') return true;
                    }
                }
                return false;
            """)
            if is_starred_js:
                return True
        except Exception:
            pass
        # Python fallback: multiple selectors, _is_favorited_state (blue/grey per element)
        for selector in (
            "button.favorites",
            "button.favorite",
            "button[class*='favorite']",
            "[class*='favorite']",
            "button[data-track-id]",
            "a[class*='favorite']",
            "[aria-label*='avorite']",
            "[data-testid*='avor']",
            "button[title*='avorite']",
        ):
            try:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                for btn in buttons:
                    if not btn or not btn.is_displayed():
                        continue
                    if _is_favorited_state(driver, btn):
                        return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _soundeo_star_log():
    """Logger for star/unstar (same as app.py soundeo_star); may have no handler if app not loaded first."""
    return logging.getLogger("soundeo_star")


def unfavorite_track_on_soundeo(
    driver,
    track_url: str,
) -> bool:
    """
    Open a Soundeo track URL and unfavorite it (click the favorite button when it is in favorited state).
    Caller must have loaded cookies and have driver on a Soundeo page. Returns True if unfavorite succeeded.
    Tries all matching buttons (main content + player bar, etc.) and clicks the first that is favorited.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    slog = _soundeo_star_log()
    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        slog.info("unfavorite_crawler: invalid url, skip")
        return False
    try:
        slog.info("unfavorite_crawler: opening url=%s", track_url[:80])
        driver.get(track_url.strip())
        time.sleep(2)
        main_button = None  # first single "button.favorites" for fallback when detection fails
        for selector in (
            "button.favorites",
            "button.favorite",
            "button[class*='favorite']",
            "button[data-track-id]",
        ):
            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)
                if selector == "button.favorites" and len(buttons) == 1 and buttons[0].is_displayed():
                    main_button = buttons[0]
                slog.info("unfavorite_crawler: selector=%s found %d buttons", selector, len(buttons))
                for i, btn in enumerate(buttons):
                    if not btn or not btn.is_displayed():
                        continue
                    try:
                        favored = _is_favorited_state(driver, btn)
                        slog.info("unfavorite_crawler: button[%d] is_displayed=True is_favorited=%s", i, favored)
                        if favored:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                            time.sleep(0.3)
                            btn.click()
                            time.sleep(0.8)
                            slog.info("unfavorite_crawler: clicked button[%d], done", i)
                            return True
                    except Exception as e:
                        slog.info("unfavorite_crawler: button[%d] check/click error: %s", i, e)
                        continue
            except Exception as e:
                slog.info("unfavorite_crawler: selector=%s wait/find error: %s", selector, e)
        if main_button is not None:
            slog.info("unfavorite_crawler: no button reported favorited; clicking single main button (toggle fallback)")
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", main_button)
                time.sleep(0.3)
                main_button.click()
                time.sleep(0.8)
                slog.info("unfavorite_crawler: toggle fallback click done")
                return True
            except Exception as e:
                slog.warning("unfavorite_crawler: toggle fallback click failed: %s", e)
        else:
            slog.warning("unfavorite_crawler: no favorited button found on page")
        # Do not use F-key fallback here: F toggles favorite state. Pressing F without
        # confirming the track is favorited could accidentally favorite instead of unfavorite.
    except Exception as e:
        slog.warning("unfavorite_crawler: exception: %s", e)
    return False


def favorite_track_by_url(driver, track_url: str) -> bool:
    """
    Open a Soundeo track URL and favorite it (click the favorite button when it is not yet favorited).
    Caller must have loaded cookies and have driver on a Soundeo page. Returns True if favorite succeeded.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        return False
    try:
        driver.get(track_url.strip())
        time.sleep(2)
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
                    if _is_favorited_state(driver, btn):
                        return True  # Already favorited
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.3)
                    btn.click()
                    time.sleep(0.8)
                    return True
            except Exception:
                pass
        # Fallback: F key toggles favorite
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        try:
            body = driver.find_element(By.TAG_NAME, "body")
            ActionChains(driver).send_keys(body, "f").perform()
            time.sleep(0.8)
            return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def verify_track_favorited(driver, track_url: str) -> Optional[bool]:
    """Visit a Soundeo track URL and return True/False for whether it's currently favorited.
    Returns None if we couldn't determine state (page error, no button found)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        return None
    try:
        driver.get(track_url.strip())
        time.sleep(2)
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
                    return _is_favorited_state(driver, btn)
            except Exception:
                pass
    except Exception:
        pass
    return None


_MIX_SUFFIX_RE = re.compile(
    r'\s*\('
    r'(?:Extended|Original|Radio|Short|Club|Dub|Instrumental|Vocal|VIP|Remix|Rework|Edit|Bootleg|Acoustic|Live)'
    r'(?:\s+(?:Mix|Version|Edit|Remix|Dub|Rework))?\)\s*$',
    re.IGNORECASE,
)


def _normalize_favorites_key(soundeo_title: str) -> str:
    """Normalize Soundeo display title to 'Artist - Title' key.

    Only strips a trailing parenthetical when it is a known mix-type suffix
    like (Extended Mix), (Original Mix), (Radio Edit), etc.
    This preserves remix info such as '(ARTBAT Remix)' that is part of the
    track identity and must match the Shazam key.
    """
    s = (soundeo_title or "").strip()
    s = _MIX_SUFFIX_RE.sub('', s).strip()
    return s or soundeo_title or ""


def _strip_all_parens_lower(key: str) -> str:
    """Strip from first '(' and lowercase — for fuzzy already_starred matching."""
    s = (key or "").strip()
    if " (" in s:
        s = s[: s.index(" (")].strip()
    return (s or key or "").lower()


def crawl_favorites_page(
    driver,
    on_progress: Optional[Callable[[str], None]] = None,
    target_keys: Optional[set] = None,
    max_pages: Optional[int] = None,
) -> Union[List[Dict], Dict]:
    """
    Crawl https://soundeo.com/account/favorites (source of truth for starred).
    Returns list of {"key": "Artist - Title", "url": track URL, "soundeo_title": exact text from page}.

    - If target_keys is set (e.g. to_download keys during sync): stop as soon as every key in
      target_keys has been seen in the crawl (newest-first, so recent tracks are found early).
    - If max_pages is set: stop after that many pages (safety cap when using target_keys).
    - If neither is set: crawl all pages (full scan, e.g. for "Scan Soundeo favorites" button).
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    seen_urls = set()
    out: List[Dict] = []
    page = 1
    page_limit = max_pages if max_pages is not None else 200
    base_url = FAVORITES_URL
    # When looking for specific keys (sync): stop after this many pages with no target keys found (newest-first, so no need to scan older pages).
    pages_without_target = 0
    CONSECUTIVE_PAGES_WITHOUT_TARGET = 3
    # No early-out: respect the time bracket (Last month=3 pages, 2 months=6, 3 months=10, All time=page_limit).
    # We do not stop on "consecutive empty pages"; we try every page up to page_limit (or max_pages).
    PAGE_LOAD_WAIT = 5  # seconds to wait for track links to appear

    consecutive_empty = 0

    while page <= page_limit:
        if _sync_stop_requested:
            break
        # Some sites use ?page=N, others /path/page/N or /path/N
        if page == 1:
            url = base_url
        else:
            url = f"{base_url}?page={page}"
        if on_progress:
            try:
                on_progress(f"Loading favorites page {page}...", page)
            except TypeError:
                on_progress(f"Loading favorites page {page}...")
        driver.get(url)
        # Give the page time to render (especially page 2+; some sites load content via JS)
        wait_sec = 4 if page > 1 else 2
        time.sleep(wait_sec)
        try:
            WebDriverWait(driver, PAGE_LOAD_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/track/"], body'))
            )
        except Exception:
            pass
        time.sleep(1)
        try:
            page_title = (driver.title or "").lower()
            if "404" in page_title or "not found" in page_title:
                break
        except Exception:
            pass
        if page == 1:
            if _is_redirected_to_login(driver):
                return {"error": "not_logged_in", "favorites": []}
        try:
            links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/track/"]')
        except Exception:
            links = []
        if not links and page == 1:
            break
        added = 0
        added_target = 0
        for lnk in links:
            try:
                href = (lnk.get_attribute("href") or "").strip()
                if not href or href in seen_urls:
                    continue
                txt = (lnk.text or "").strip()
                if not txt or len(txt) < 2:
                    continue
                seen_urls.add(href)
                key = _normalize_favorites_key(txt)
                out.append({"key": key, "url": href, "soundeo_title": txt})
                added += 1
                if target_keys and key in target_keys:
                    added_target += 1
            except Exception:
                pass
        if added == 0 and page > 1:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                break
        else:
            consecutive_empty = 0
        if target_keys:
            if added_target > 0:
                pages_without_target = 0
            else:
                pages_without_target += 1
            keys_seen = {item["key"] for item in out}
            if target_keys <= keys_seen:
                break
            if pages_without_target >= CONSECUTIVE_PAGES_WITHOUT_TARGET:
                break
        if max_pages is not None and page >= max_pages:
            break
        # Prefer finding a "Next" / pagination link; if not found, still try next page by URL (many sites use ?page=2)
        next_link = None
        try:
            for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="page="]'):
                atext = (a.text or "").strip().lower()
                if atext in ("next", "»", ">") or (atext.isdigit() and int(atext) == page + 1):
                    next_link = a
                    break
            if not next_link:
                for a in driver.find_elements(By.CSS_SELECTOR, f'a[href*="page={page + 1}"]'):
                    next_link = a
                    break
        except Exception:
            pass
        # If no Next link found, still try loading next page by URL so we don't stop after 1 page
        page += 1
    return out


def _chrome_session_error_message(exc: Exception) -> str:
    """User-friendly message when Chrome fails to start (e.g. profile in use)."""
    msg = str(exc).lower()
    if "session not created" in msg or "chrome instance exited" in msg or "user data directory" in msg:
        return (
            "Chrome could not start (profile may be in use). "
            "Close any Chrome window using the Soundeo profile, or wait for the previous sync to finish, then try again."
        )
    return str(exc)


def crawl_soundeo_favorites(
    cookies_path: str,
    headed: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
    max_pages: Optional[int] = None,
    verify_tracks: Optional[List[Dict]] = None,
) -> Dict:
    """
    Open browser, load session, crawl /account/favorites, then cross-check, return result.

    Returns {"ok": True, "favorites": [...], "verified": [...]} or {"ok": False, "error": "..."}.

    verify_tracks: list of {"key": ..., "url": ...} for tracks previously starred in the app.
    After crawling, any track in verify_tracks NOT found in the crawl is visited on Soundeo
    to definitively check whether it is still favorited (cross-check).
    """
    clear_sync_stop_request()
    driver = None
    try:
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
    except Exception as e:
        return {"ok": False, "error": _chrome_session_error_message(e)}
    try:
        if not load_cookies(driver, cookies_path):
            return {"ok": False, "error": "No saved session. Save Soundeo session first in Settings."}
        favorites = crawl_favorites_page(driver, on_progress=on_progress, max_pages=max_pages)
        if isinstance(favorites, dict) and favorites.get("error") == "not_logged_in":
            return {"ok": False, "error": "Soundeo session expired. Please reconnect in Settings."}
        if not favorites:
            try:
                current = (driver.current_url or "").strip().lower()
                if "logoreg" in current or "/login" in current or "/account/log" in current:
                    return {"ok": False, "error": "Soundeo session expired. Please reconnect in Settings."}
            except Exception:
                pass
        stopped = _sync_stop_requested

        # Cross-check: verify tracks that were starred but not found in crawl
        verified: List[Dict] = []
        if verify_tracks and not stopped:
            crawled_keys_lower = {(item.get("key") or "").lower() for item in favorites}
            crawled_norms = set()
            for item in favorites:
                k = item.get("key") or ""
                s = k.lower()
                if " (" in s:
                    s = s[:s.index(" (")].strip()
                crawled_norms.add(s)
                # Also add deep-norm (sorted artists, & → ,)
                dn = s.replace(' & ', ', ')
                if ' - ' in dn:
                    ap, tp = dn.split(' - ', 1)
                    dn = ', '.join(sorted(a.strip() for a in ap.split(', ') if a.strip())) + ' - ' + tp
                crawled_norms.add(dn)

            to_check = []
            for t in verify_tracks:
                k = (t.get("key") or "").lower()
                kn = k
                if " (" in kn:
                    kn = kn[:kn.index(" (")].strip()
                kd = kn.replace(' & ', ', ')
                if ' - ' in kd:
                    ap, tp = kd.split(' - ', 1)
                    kd = ', '.join(sorted(a.strip() for a in ap.split(', ') if a.strip())) + ' - ' + tp
                if k not in crawled_keys_lower and kn not in crawled_norms and kd not in crawled_norms:
                    to_check.append(t)

            for i, t in enumerate(to_check):
                if _sync_stop_requested:
                    break
                key = t.get("key", "")
                url = t.get("url", "")
                if not url:
                    continue
                try:
                    if on_progress:
                        try:
                            on_progress(f"Cross-checking {i+1}/{len(to_check)}: {key[:45]}...")
                        except TypeError:
                            on_progress(f"Cross-checking {i+1}/{len(to_check)}: {key[:45]}...")
                except Exception:
                    pass
                result = verify_track_favorited(driver, url)
                verified.append({"key": key, "url": url, "still_favorited": result})

        return {"ok": True, "favorites": favorites, "stopped": stopped, "verified": verified}
    except Exception as e:
        err_msg = str(e).lower()
        if "no such window" in err_msg or "target window" in err_msg or "web view not found" in err_msg:
            return {"ok": False, "error": "Browser was closed.", "stopped": True}
        return {"ok": False, "error": _chrome_session_error_message(e) if "session not created" in err_msg or "chrome instance exited" in err_msg else str(e)}
    finally:
        _graceful_quit(driver)


# Stop requested from UI (checked between tracks)
_sync_stop_requested = False


def request_sync_stop() -> None:
    """Request the running sync to stop after the current track."""
    global _sync_stop_requested
    _sync_stop_requested = True


def clear_sync_stop_request() -> None:
    global _sync_stop_requested
    _sync_stop_requested = False


def is_sync_stop_requested() -> bool:
    """Return whether the running sync was asked to stop."""
    return _sync_stop_requested


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
    Open browser to Soundeo login page so user can log in, then save cookies when done_event is set.
    Uses persistent browser profile so login state is remembered.
    """
    global _save_session_last_error
    _save_session_last_error = None
    driver = None
    try:
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
    except Exception as e:
        _save_session_last_error = str(e)
        return {"success": False, "message": "Browser could not start. Close all Chrome windows and try again.", "error": str(e)}
    try:
        cookies_path = os.path.abspath(cookies_path)
        driver.get(SOUNDEO_LOGIN)
        time.sleep(2)
        if done_event is not None:
            done_event.wait(timeout=300)
        else:
            input("Log in to Soundeo in the browser, then press Enter here to save session...")
        driver.get(SOUNDEO_BASE)
        time.sleep(3)
        ok = save_cookies(driver, cookies_path)
        return {"success": ok, "message": "Session saved" if ok else "Failed to save cookies"}
    finally:
        _graceful_quit(driver)


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
    on_progress: Optional[Callable[[int, int, str, Optional[str]], None]] = None,
    already_starred: Optional[set] = None,
    crawl_favorites_first: bool = True,
    max_favorites_pages: Optional[int] = None,
) -> Dict:
    """
    For each track: search, open, favorite (unless already_starred).
    Sync assumes Scan was run (or we crawl at start): the crawl at sync start is the source
    of truth for already_starred. We never unfavorite: for keys in already_starred we do not
    open the track page or click the star.
    If crawl_favorites_first=True (default), crawl /account/favorites first and use that as
    source of truth for already_starred; tracks on that page are never opened.
    Stops if request_sync_stop() was called (check between tracks).
    """
    global _sync_stop_requested
    clear_sync_stop_request()
    driver = None
    try:
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
    except Exception as e:
        return {"error": _chrome_session_error_message(e)}

    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "soundeo_match_scores": {}, "crawled_favorites": [], "stopped": False}

    try:
        if not load_cookies(driver, cookies_path):
            return {"error": "No saved session. Save Soundeo session first in Settings."}

        if crawl_favorites_first:
            def crawl_prog(msg, page=None):
                if on_progress:
                    on_progress(0, len(tracks), msg, None)
            target_keys = set(f"{t.get('artist', '')} - {t.get('title', '')}" for t in tracks)
            crawled = crawl_favorites_page(
                driver,
                on_progress=crawl_prog,
                target_keys=target_keys if target_keys else None,
                max_pages=max_favorites_pages if max_favorites_pages is not None else 30,
            )
            if isinstance(crawled, dict) and crawled.get("error") == "not_logged_in":
                return {"error": "Soundeo session expired or you are logged out. Please use Save session to log in again."}
            crawled_list = crawled if isinstance(crawled, list) else []
            results["crawled_favorites"] = crawled_list
            already_starred = set()
            for item in crawled_list:
                k = item["key"]
                already_starred.add(k)
                already_starred.add(k.lower())
                # Also add deep-normalized form for & vs , and artist order matching
                dn = _strip_all_parens_lower(k).replace(' & ', ', ')
                if ' - ' in dn:
                    ap, tp = dn.split(' - ', 1)
                    arts = ', '.join(sorted(a.strip() for a in ap.split(', ') if a.strip()))
                    already_starred.add(arts + ' - ' + tp)
                else:
                    already_starred.add(dn)
        elif already_starred is None:
            already_starred = set()

        for i, t in enumerate(tracks):
            if _sync_stop_requested:
                results["stopped"] = True
                if on_progress:
                    on_progress(i, len(tracks), "Stopped by user", None, None)
                break
            artist = t.get("artist", "")
            title = t.get("title", "")
            key = f"{artist} - {title}"
            progress_msg = f"Processing {i+1}/{len(tracks)}: {artist} - {title}"

            def prog(s, _key=key):
                if on_progress:
                    on_progress(i + 1, len(tracks), s, None, _key)

            out = find_and_favorite_track(
                driver, artist, title,
                on_progress=prog, delay=2.5,
                already_starred=already_starred,
            )
            if out:
                url = out[0] if isinstance(out, tuple) else out
                display_text = (out[1] if isinstance(out, tuple) and len(out) > 1 else "") or ""
                match_score = (out[2] if isinstance(out, tuple) and len(out) > 2 else None)
                results["done"] += 1
                results["urls"][key] = url
                results["soundeo_titles"][key] = display_text or key
                if match_score is not None:
                    results["soundeo_match_scores"][key] = round(match_score, 3)
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Favorited: {artist} - {title}", url, key)
            else:
                results["failed"] += 1
                results["errors"].append(f"Not found: {artist} - {title}")
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Not found: {artist} - {title}", None, key)

            time.sleep(2.5)

    except Exception as e:
        err_msg = str(e).lower()
        if "no such window" in err_msg or "target window" in err_msg or "web view not found" in err_msg:
            results["error"] = "Browser was closed."
            results["stopped"] = True
        else:
            results["error"] = _chrome_session_error_message(e) if "session not created" in err_msg or "chrome instance exited" in err_msg else str(e)
    finally:
        _graceful_quit(driver)

    return results


def run_search_tracks(
    tracks: List[Dict[str, str]],
    cookies_path: str,
    headed: bool = False,
    on_progress: Optional[Callable[[int, int, str, Optional[str]], None]] = None,
    skip_keys: Optional[set] = None,
) -> Dict:
    """
    Search Soundeo for each track (no favorite). Returns urls and soundeo_titles.
    skip_keys: set of "Artist - Title" keys that already have a URL; those tracks are skipped.
    Stops if request_sync_stop() was called.
    """
    global _sync_stop_requested
    clear_sync_stop_request()
    skip_keys = skip_keys or set()
    driver = None
    try:
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
    except Exception as e:
        return {"error": _chrome_session_error_message(e)}

    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "soundeo_match_scores": {}, "stopped": False}

    try:
        if not load_cookies(driver, cookies_path):
            return {"error": "No saved session. Save Soundeo session first in Settings."}

        for i, t in enumerate(tracks):
            if _sync_stop_requested:
                results["stopped"] = True
                if on_progress:
                    on_progress(i, len(tracks), "Stopped by user", None, None)
                break
            artist = t.get("artist", "")
            title = t.get("title", "")
            key = f"{artist} - {title}"
            key_lower = key.lower()
            if key in skip_keys or key_lower in skip_keys:
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Skipped (already found): {artist} - {title}", None, key)
                continue

            def prog(s, _key=key):
                if on_progress:
                    on_progress(i + 1, len(tracks), s, None, _key)

            out = find_track_on_soundeo(driver, artist, title, on_progress=prog, delay=2.5)
            if out:
                url = out[0] if isinstance(out, tuple) else out
                display_text = (out[1] if isinstance(out, tuple) and len(out) > 1 else "") or ""
                match_score = (out[2] if isinstance(out, tuple) and len(out) > 2 else None)
                results["done"] += 1
                results["urls"][key] = url
                results["soundeo_titles"][key] = display_text or key
                if match_score is not None:
                    results["soundeo_match_scores"][key] = round(match_score, 3)
                if on_progress:
                    kwargs = {}
                    if display_text:
                        kwargs["soundeo_title"] = display_text
                    if match_score is not None:
                        kwargs["soundeo_match_score"] = round(match_score, 3)
                    on_progress(i + 1, len(tracks), f"Found: {artist} - {title}", url, key, **kwargs)
            else:
                if results["done"] == 0 and _is_redirected_to_login(driver):
                    results["error"] = "Soundeo session expired or you are logged out. Please use Save session to log in again."
                    break
                results["failed"] += 1
                results["errors"].append(f"Not found: {artist} - {title}")
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Not found: {artist} - {title}", None, key)

            time.sleep(2.5)

    except Exception as e:
        err_msg = str(e).lower()
        if "no such window" in err_msg or "target window" in err_msg or "web view not found" in err_msg:
            results["error"] = "Browser was closed."
            results["stopped"] = True
        else:
            results["error"] = _chrome_session_error_message(e) if "session not created" in err_msg or "chrome instance exited" in err_msg else str(e)
    finally:
        _graceful_quit(driver)

    return results


def run_search_tracks_http(
    tracks: List[Dict[str, str]],
    cookies_path: str,
    on_progress: Optional[Callable[[int, int, str, Optional[str]], None]] = None,
    skip_keys: Optional[set] = None,
) -> Dict:
    """
    Search Soundeo for each track via HTTP (no browser). Same contract as run_search_tracks:
    returns {done, failed, urls, soundeo_titles, error?, stopped}. Does not favorite.
    Use when browser/Selenium path fails or to avoid opening Chrome.
    """
    global _sync_stop_requested
    clear_sync_stop_request()
    skip_keys = skip_keys or set()
    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "soundeo_match_scores": {}, "stopped": False}

    if not os.path.exists(os.path.abspath(cookies_path)):
        return {"error": "No saved session. Save Soundeo session first in Settings."}

    for i, t in enumerate(tracks):
        if _sync_stop_requested:
            results["stopped"] = True
            if on_progress:
                on_progress(i, len(tracks), "Stopped by user", None, None)
            break
        artist = t.get("artist", "")
        title = t.get("title", "")
        key = f"{artist} - {title}"
        key_lower = key.lower()
        if key in skip_keys or key_lower in skip_keys:
            if on_progress:
                on_progress(i + 1, len(tracks), f"Skipped (already found): {artist} - {title}", None, key)
            continue

        if on_progress:
            on_progress(i + 1, len(tracks), f"Searching: {artist} - {title}", None, key)
        best_url = None
        best_title = None
        best_score = -1
        for q in _search_queries(artist, title):
            try:
                search_results = soundeo_api_search(q, cookies_path)
            except Exception:
                continue
            for r in search_results[:15]:
                score = _best_match_score({}, r["title"], artist, title) + _extended_preference_bonus(r["title"])
                if score > best_score:
                    best_score = score
                    best_url = r.get("href")
                    best_title = r.get("title") or key

        if best_url and best_score >= _MATCH_THRESHOLD:
            results["done"] += 1
            results["urls"][key] = best_url
            results["soundeo_titles"][key] = best_title or key
            results["soundeo_match_scores"][key] = round(best_score, 3)
            if on_progress:
                on_progress(
                    i + 1, len(tracks), f"Found: {artist} - {title}", best_url, key,
                    soundeo_title=best_title or key,
                    soundeo_match_score=round(best_score, 3),
                )
        else:
            results["failed"] += 1
            results["errors"].append(f"Not found: {artist} - {title}")
            if on_progress:
                on_progress(i + 1, len(tracks), f"Not found: {artist} - {title}", None, key)

        time.sleep(0.8)

    return results


# ---------------------------------------------------------------------------
# Soundeo HTTP API — no browser needed
# ---------------------------------------------------------------------------

def _get_soundeo_session(cookies_path: str):
    """Build a requests.Session with saved Soundeo cookies."""
    import requests as req

    cookies_path = os.path.abspath(cookies_path)
    if not os.path.exists(cookies_path):
        return None
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            raw_cookies = json.load(f)
    except Exception:
        return None
    session = req.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    for c in raw_cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        domain = c.get("domain", "soundeo.com")
        if name and value:
            session.cookies.set(name, value, domain=domain.lstrip("."))
    return session


def extract_track_id(url_or_html: str) -> Optional[str]:
    """Extract numeric Soundeo track ID from a URL like /track/artist-title-12345.html."""
    import re
    m = re.search(r"-(\d{4,})\.html", url_or_html or "")
    return m.group(1) if m else None


def soundeo_api_toggle_favorite(track_id: str, cookies_path: str) -> Dict:
    """
    Toggle favorite state via Soundeo HTTP API.  Returns e.g.
    {"ok": True, "result": "favored"} or {"ok": True, "result": "unfavored"}.
    Logs when response is non-JSON so we can fall back to crawler.
    """
    session = _get_soundeo_session(cookies_path)
    if not session:
        return {"ok": False, "error": "No saved session"}
    try:
        resp = session.post(
            f"{SOUNDEO_BASE}/tracks/favor/{track_id}",
            data={},
            timeout=10,
        )
        text = (resp.text or "").strip()
        try:
            data = resp.json() if text else {}
        except Exception as parse_err:
            _soundeo_star_log().info(
                "toggle_favorite HTTP: non-JSON response status=%s body_len=%s preview=%s",
                resp.status_code, len(resp.content or b""), (text[:120] if text else "(empty)"),
            )
            return {"ok": False, "error": str(parse_err)}
        if data.get("success"):
            return {"ok": True, "result": data.get("result", "unknown")}
        return {"ok": False, "error": data.get("message", "API error")}
    except Exception as e:
        _soundeo_star_log().info("toggle_favorite HTTP: exception %s", e)
        return {"ok": False, "error": str(e)}


def soundeo_api_get_favorite_state(track_url: str, cookies_path: str) -> Optional[bool]:
    """
    GET the track page and parse HTML to read current favorite state on Soundeo.
    Returns True if favored, False if not favored, None if we couldn't determine.
    Handles detail page: attribute order may differ from list pages.
    """
    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        return None
    session = _get_soundeo_session(cookies_path)
    if not session:
        return None
    try:
        resp = session.get(track_url.strip(), timeout=10)
        if resp.status_code != 200:
            return None
        text = resp.text or ""
        # List-style: class="favorites..." then data-track-id (or reverse)
        for _cls, _tid in re.findall(
            r'<button[^>]*class="favorites([^"]*?)"[^>]*data-track-id="(\d+)"',
            text,
        ):
            return "favored" in _cls
        for _tid, _cls in re.findall(
            r'<button[^>]*data-track-id="(\d+)"[^>]*class="favorites([^"]*?)"[^>]*>',
            text,
        ):
            return "favored" in _cls
        # Any tag that has both data-track-id and favorite in class (detail page)
        for tag in re.findall(r'<button[^>]*(?:data-track-id="\d+")[^>]*>', text):
            if "favorite" in tag.lower():
                return "favored" in tag.lower() or "active" in tag.lower()
        for tag in re.findall(r'<[^>]*(?:class="[^"]*favorite[^"]*")[^>]*data-track-id="\d+"[^>]*>', text, re.I):
            return "favored" in tag.lower() or "active" in tag.lower()
    except Exception:
        pass
    return None


def get_track_id_from_page(track_url: str, cookies_path: str) -> Optional[str]:
    """
    GET the track page and parse HTML for data-track-id. Used when the URL does not
    contain the ID (e.g. extract_track_id returns None). Single source of truth for
    IDs is status cache; this is used to backfill or resolve on demand.
    Returns the numeric track ID string or None.
    """
    if not track_url or not track_url.strip().startswith(("https://soundeo.com/", "http://soundeo.com/")):
        return None
    tid = extract_track_id(track_url)
    if tid:
        return tid
    session = _get_soundeo_session(cookies_path)
    if not session:
        return None
    try:
        resp = session.get(track_url.strip(), timeout=10)
        if resp.status_code != 200:
            return None
        text = resp.text or ""
        for _cls, _tid in re.findall(
            r'<button[^>]*class="favorites([^"]*?)"[^>]*data-track-id="(\d+)"',
            text,
        ):
            return _tid
        for _tid, _cls in re.findall(
            r'<button[^>]*data-track-id="(\d+)"[^>]*class="favorites([^"]*?)"[^>]*>',
            text,
        ):
            return _tid
        m = re.search(r'data-track-id="(\d+)"', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def soundeo_api_set_favorite(
    track_id: str, cookies_path: str, favored: bool, track_url: Optional[str] = None
) -> Dict:
    """
    Set favorite state on Soundeo. First checks actual state on Soundeo (if track_url given),
    then toggles only when current state != desired.
    favored=True -> ensure track is favored; favored=False -> ensure track is unfavored.
    Returns {"ok": True, "result": "favored"|"unfavored"} or {"ok": False, "error": "..."}.
    """
    slog = _soundeo_star_log()
    if track_url:
        current = soundeo_api_get_favorite_state(track_url, cookies_path)
        slog.info("set_favorite HTTP: get_favorite_state=%s (desired favored=%s)", current, favored)
        if current is not None and current == favored:
            slog.info("set_favorite HTTP: skip toggle (already in desired state)")
            return {"ok": True, "result": "favored" if favored else "unfavored"}
    # Need to change state: toggle once (we know we're out of sync or couldn't read state)
    r = soundeo_api_toggle_favorite(track_id, cookies_path)
    slog.info("set_favorite HTTP: toggle1 ok=%s result=%s error=%s", r.get("ok"), r.get("result"), r.get("error"))
    if not r.get("ok"):
        return r
    if (r.get("result") or "").lower() == ("favored" if favored else "unfavored"):
        return r
    r2 = soundeo_api_toggle_favorite(track_id, cookies_path)
    slog.info("set_favorite HTTP: toggle2 ok=%s result=%s", r2.get("ok"), r2.get("result"))
    return r2 if r2.get("ok") else r


def _parse_track_links_from_html(html: str) -> List[Tuple[str, str]]:
    """Parse track links from Soundeo HTML. Returns list of (href, link_text)."""
    import re
    import html as htmllib
    links = re.findall(
        r'<a[^>]+href="(/track/[^"]+)"[^>]*>([^<]+)</a>', html
    )
    return [(href, htmllib.unescape(txt).strip()) for href, txt in links]


def soundeo_api_search(query: str, cookies_path: str) -> List[Dict]:
    """
    Search Soundeo via HTTP and return parsed results.
    Each result: {"track_id", "title", "href", "favored": bool}.
    Tries /list/tracks first, then /search?q= (same as browser search) if no results.
    """
    import re
    import html as htmllib

    session = _get_soundeo_session(cookies_path)
    if not session:
        return []
    encoded = urllib.parse.quote(query)

    def _parse_results(resp_text: str) -> List[Dict]:
        links = _parse_track_links_from_html(resp_text)
        fav_map = {}
        for cls, tid in re.findall(
            r'<button[^>]*class="favorites([^"]*?)"[^>]*data-track-id="(\d+)"',
            resp_text,
        ):
            fav_map[tid] = "favored" in cls
        results = []
        seen_ids = set()
        for href, txt in links:
            tid = extract_track_id(href)
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            results.append({
                "track_id": tid,
                "title": txt,
                "href": f"{SOUNDEO_BASE}{href}",
                "favored": fav_map.get(tid, False),
            })
        return results

    # Prefer /list/tracks (track list with filter)
    url_list = f"{SOUNDEO_BASE}/list/tracks?searchFilter={encoded}&availableFilter=1"
    try:
        resp = session.get(url_list, timeout=10)
        if resp.status_code == 200:
            results = _parse_results(resp.text)
            if results:
                return results
    except Exception:
        pass

    # Fallback: /search?q= (same URL as browser search, e.g. search?q=rezarin+androme)
    url_search = f"{SOUNDEO_BASE}/search?q={encoded}"
    try:
        resp = session.get(url_search, timeout=10)
        if resp.status_code == 200:
            return _parse_results(resp.text)
    except Exception:
        pass
    return []


def soundeo_api_search_and_favorite(
    artist: str,
    title: str,
    cookies_path: str,
) -> Dict:
    """
    Search Soundeo for a track via HTTP, find the best match, and favorite it.
    Returns {"ok": True, "url", "display_text", "track_id"} or {"ok": False, "error"}.
    No browser needed.
    """
    queries = _search_queries(artist, title)
    for q in queries:
        results = soundeo_api_search(q, cookies_path)
        if not results:
            continue
        best = None
        best_score = -1
        for r in results[:15]:
            score = _best_match_score({}, r["title"], artist, title) + _extended_preference_bonus(r["title"])
            if score > best_score:
                best_score = score
                best = r
        if best and best_score >= 0.3:
            if not best["favored"]:
                toggle = soundeo_api_toggle_favorite(best["track_id"], cookies_path)
                if not toggle.get("ok"):
                    return {"ok": False, "error": toggle.get("error", "Toggle failed")}
            return {
                "ok": True,
                "url": best["href"],
                "display_text": best["title"],
                "track_id": best["track_id"],
                "already_favored": best["favored"],
            }

    return {"ok": False, "error": f"Not found: {artist} - {title}"}
