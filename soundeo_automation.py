"""
Soundeo browser automation via Selenium.
Single driver path: either attach to running Chrome or launch Chrome with a persistent profile.
Config via config_shazam.get_soundeo_browser_config() (mode "attach" | "launch").
"""
import os
import re

import json
import time
import threading
import urllib.parse
from typing import List, Dict, Optional, Callable

_ONE_YEAR = 365 * 24 * 60 * 60


def _clean_profile_dir(profile_path: str) -> None:
    """Remove stale lock files and corrupted marker files from a Chrome profile directory."""
    if not profile_path or not os.path.isdir(profile_path):
        return
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_path, name)
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

    def _build_opts():
        o = Options()
        if use_persistent_profile and user_data_dir:
            o.add_argument("--user-data-dir=" + user_data_dir)
            profile_dir = cfg.get("profile_directory")
            if profile_dir:
                o.add_argument("--profile-directory=" + profile_dir)
        if headless:
            o.add_argument("--headless=new")
        o.add_argument("--window-size=1280,900")
        o.add_argument("--disable-gpu")
        o.add_argument("--no-sandbox")
        o.add_experimental_option("excludeSwitches", ["enable-automation"])
        o.add_argument("--disable-blink-features=AutomationControlled")
        return o

    _clean_profile_dir(user_data_dir)

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
        driver.get(SOUNDEO_BASE)
        time.sleep(2)
        return True
    except Exception:
        return False


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


def _extended_preference_bonus(link_text: str) -> float:
    """
    Prefer Extended Mix/Version when multiple links match (explicit rule).
    Only add bonus for Extended; do not penalize Original so we still pick it if it's the only match.
    """
    if not link_text:
        return 0.0
    if "extended" in link_text.lower():
        return 0.5
    return 0.0


def find_track_on_soundeo(
    driver,
    artist: str,
    title: str,
    on_progress: Optional[Callable[[str], None]] = None,
    delay: float = 2.5,
) -> Optional[tuple]:
    """
    Search for track on Soundeo and return (url, display_text) of the best match.
    Does NOT open the track page or click favorite — use for "Search on Soundeo" only.
    """
    queries = _search_queries(artist, title)
    for q in queries:
        encoded = urllib.parse.quote(q)
        url = f"{TRACK_LIST_URL}?searchFilter={encoded}&availableFilter=1"
        if on_progress:
            on_progress(f"Searching: {q}")
        driver.get(url)
        time.sleep(1.5)

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
                score = _best_match_score({}, txt, artist, title) + _extended_preference_bonus(txt)
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
                display_text = (best_link.text or "").strip()
                return (href, display_text)
            except Exception:
                pass

        time.sleep(delay)

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
    anything (to avoid ever un-starring). Returns (url, display_text) or None.
    """
    queries = _search_queries(artist, title)
    for q in queries:
        encoded = urllib.parse.quote(q)
        url = f"{TRACK_LIST_URL}?searchFilter={encoded}&availableFilter=1"
        if on_progress:
            on_progress(f"Searching: {q}")
        driver.get(url)
        time.sleep(1.5)

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
                score = _best_match_score({}, txt, artist, title) + _extended_preference_bonus(txt)
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
                display_text = (best_link.text or "").strip()
                key = f"{artist} - {title}"
                if already_starred:
                    dn = _strip_all_parens_lower(key).replace(' & ', ', ')
                    if ' - ' in dn:
                        ap, tp = dn.split(' - ', 1)
                        dn = ', '.join(sorted(a.strip() for a in ap.split(', ') if a.strip())) + ' - ' + tp
                    if key in already_starred or key.lower() in already_starred or dn in already_starred:
                        if on_progress:
                            on_progress(f"Already starred: {key[:50]}...")
                        return (href, display_text)

                if on_progress:
                    on_progress(f"Opening: {best_link.text[:50]}...")
                driver.get(href)
                time.sleep(2)

                # Click favorite button only if not already favorited (blue/starred). Never un-favorite.
                favorited = False
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

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

                return (href, display_text)
            except Exception:
                pass

        time.sleep(delay)

    return None


def _is_favorited_state(driver, btn) -> bool:
    """True if button/element indicates already favorited (e.g. blue/active). Used to avoid un-favoriting in sync and to detect state for unfavorite action."""
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


def unfavorite_track_on_soundeo(
    driver,
    track_url: str,
) -> bool:
    """
    Open a Soundeo track URL and unfavorite it (click the favorite button when it is in favorited state).
    Caller must have loaded cookies and have driver on a Soundeo page. Returns True if unfavorite succeeded.
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
                if btn and btn.is_displayed() and _is_favorited_state(driver, btn):
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.3)
                    btn.click()
                    time.sleep(0.8)
                    return True
            except Exception:
                pass
        # Do not use F-key fallback here: F toggles favorite state. Pressing F without
        # confirming the track is favorited could accidentally favorite instead of unfavorite.
    except Exception:
        pass
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
) -> List[Dict]:
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
            try:
                current_url = (driver.current_url or "").strip().lower()
                if "logoreg" in current_url or "/login" in current_url or "/account/log" in current_url:
                    break
            except Exception:
                pass
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
        if not verify_logged_in(driver):
            return {"ok": False, "error": "Soundeo session expired or you are logged out. Please use Save session to log in again."}
        favorites = crawl_favorites_page(driver, on_progress=on_progress, max_pages=max_pages)
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

    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "crawled_favorites": [], "stopped": False}

    try:
        if not load_cookies(driver, cookies_path):
            return {"error": "No saved session. Save Soundeo session first in Settings."}
        if not verify_logged_in(driver):
            return {"error": "Soundeo session expired or you are logged out. Please use Save session to log in again."}

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
            results["crawled_favorites"] = crawled
            already_starred = set()
            for item in crawled:
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
                results["done"] += 1
                results["urls"][key] = url
                results["soundeo_titles"][key] = display_text or key
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

    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "stopped": False}

    try:
        if not load_cookies(driver, cookies_path):
            return {"error": "No saved session. Save Soundeo session first in Settings."}
        if not verify_logged_in(driver):
            return {"error": "Soundeo session expired or you are logged out. Please use Save session to log in again."}

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
                results["done"] += 1
                results["urls"][key] = url
                results["soundeo_titles"][key] = display_text or key
                if on_progress:
                    on_progress(i + 1, len(tracks), f"Found: {artist} - {title}", url, key)
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
    results = {"done": 0, "failed": 0, "errors": [], "urls": {}, "soundeo_titles": {}, "stopped": False}

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
                if score > best_score and score >= 0.3:
                    best_score = score
                    best_url = r.get("href")
                    best_title = r.get("title") or key

        if best_url and best_score >= 0.3:
            results["done"] += 1
            results["urls"][key] = best_url
            results["soundeo_titles"][key] = best_title or key
            if on_progress:
                on_progress(i + 1, len(tracks), f"Found: {artist} - {title}", best_url, key)
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
        data = resp.json()
        if data.get("success"):
            return {"ok": True, "result": data.get("result", "unknown")}
        return {"ok": False, "error": data.get("message", "API error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def soundeo_api_search(query: str, cookies_path: str) -> List[Dict]:
    """
    Search Soundeo via HTTP and return parsed results.
    Each result: {"track_id", "title", "href", "favored": bool}.
    """
    import re
    import html as htmllib

    session = _get_soundeo_session(cookies_path)
    if not session:
        return []
    encoded = urllib.parse.quote(query)
    url = f"{SOUNDEO_BASE}/list/tracks?searchFilter={encoded}&availableFilter=1"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    links = re.findall(
        r'<a[^>]+href="(/track/[^"]+)"[^>]*>([^<]+)</a>', resp.text
    )
    fav_map = {}
    for cls, tid in re.findall(
        r'<button[^>]*class="favorites([^"]*?)"[^>]*data-track-id="(\d+)"',
        resp.text,
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
            "title": htmllib.unescape(txt).strip(),
            "href": f"{SOUNDEO_BASE}{href}",
            "favored": fav_map.get(tid, False),
        })
    return results


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
