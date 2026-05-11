#!/usr/bin/env python3
"""
Inspect Soundeo favorite toggle HTTP behavior: request URL, redirects, response.
Uses the same session as the app (cookies from config). Run from project root:
  python3 scripts/inspect_soundeo_toggle.py [track_id]
Default track_id: 1713144 (Eric Prydz - Niton).
"""
import os
import sys
import json

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

SOUNDEO_BASE = "https://soundeo.com"


def main():
    track_id = (sys.argv[1] if len(sys.argv) > 1 else "1713144").strip()
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import _get_soundeo_session

    cookies_path = get_soundeo_cookies_path()
    if not cookies_path or not os.path.exists(cookies_path):
        print("No cookies file. Save Soundeo session first in Settings.")
        return 1

    session = _get_soundeo_session(cookies_path)
    if not session:
        print("Could not build session from cookies.")
        return 1

    url = f"{SOUNDEO_BASE}/tracks/favor/{track_id}"
    print(f"POST {url}")
    print("Session cookies count:", len(session.cookies))
    print("Session headers:", dict(session.headers))
    print()

    # 1) With redirects (default) – what we do now
    try:
        r = session.post(url, data={}, timeout=10)
        print("--- With allow_redirects=True (default) ---")
        print("Response status:", r.status_code)
        print("Response URL (final):", r.url)
        print("Response Content-Type:", r.headers.get("Content-Type"))
        print("History (redirects):", [(h.status_code, h.headers.get("Location")) for h in r.history])
        body = (r.text or "")[:500]
        print("Body preview:", repr(body))
        try:
            j = r.json()
            print("JSON parsed:", j)
        except Exception as e:
            print("JSON parse error:", e)
    except Exception as e:
        print("Request error:", e)

    print()

    # 2) Without following redirects – see if POST returns 302
    try:
        r2 = session.post(url, data={}, timeout=10, allow_redirects=False)
        print("--- With allow_redirects=False ---")
        print("Response status:", r2.status_code)
        print("Location:", r2.headers.get("Location"))
        print("Content-Type:", r2.headers.get("Content-Type"))
        body2 = (r2.text or "")[:300]
        print("Body preview:", repr(body2))
    except Exception as e:
        print("Request error (no redirects):", e)

    print()

    # 3) With Accept: application/json and Referer
    try:
        session2 = _get_soundeo_session(cookies_path)
        session2.headers["Accept"] = "application/json"
        session2.headers["Referer"] = f"{SOUNDEO_BASE}/"
        session2.headers["Origin"] = SOUNDEO_BASE
        r3 = session2.post(url, data={}, timeout=10, allow_redirects=False)
        print("--- With Accept: application/json, Referer, allow_redirects=False ---")
        print("Response status:", r3.status_code)
        print("Location:", r3.headers.get("Location"))
        print("Content-Type:", r3.headers.get("Content-Type"))
        body3 = (r3.text or "")[:400]
        print("Body preview:", repr(body3))
        if r3.status_code == 200:
            try:
                j3 = r3.json()
                print("JSON:", j3)
            except Exception as e:
                print("JSON parse:", e)
    except Exception as e:
        print("Request error:", e)

    # 4) GET track page and look for CSRF/token, then POST with Referer = track page
    import re
    track_url = f"{SOUNDEO_BASE}/track/eric-prydz-niton-the-reason-extended-mix-{track_id}.html"
    try:
        sess = _get_soundeo_session(cookies_path)
        sess.headers["Accept"] = "text/html,application/json"
        g = sess.get(track_url, timeout=10)
        print("\n--- GET track page ---")
        print("Status:", g.status_code, "URL:", g.url)
        if g.status_code == 200:
            html = g.text or ""
            # Common patterns for CSRF / token
            for name, pat in [
                ("csrf-token meta", re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)),
                ("_token input", re.search(r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']', html, re.I)),
                ("token in script", re.search(r'["\'](?:csrf|token|_token)["\']\s*:\s*["\']([^"\']+)["\']', html, re.I)),
                ("data-track-id", re.findall(r'data-track-id=["\'](\d+)["\']', html)[:3]),
            ]:
                if isinstance(pat, re.Match) and pat:
                    print(f"  {name}:", pat.group(1)[:80] if pat.lastindex else pat.group(0)[:80])
                elif isinstance(pat, list):
                    print(f"  {name}:", pat)
            # Try POST with Referer = track page and X-Requested-With
            sess2 = _get_soundeo_session(cookies_path)
            sess2.headers["Accept"] = "application/json"
            sess2.headers["Referer"] = track_url
            sess2.headers["Origin"] = SOUNDEO_BASE
            sess2.headers["X-Requested-With"] = "XMLHttpRequest"
            r4 = sess2.post(url, data={}, timeout=10, allow_redirects=False)
            print("\n--- POST with Referer=track URL, X-Requested-With: XMLHttpRequest ---")
            print("Status:", r4.status_code, "Content-Type:", r4.headers.get("Content-Type"))
            print("Body:", (r4.text or "")[:500])
            if r4.status_code == 200:
                try:
                    print("JSON:", r4.json())
                except Exception:
                    pass
        else:
            print("Track page not 200, skip token search")
    except Exception as e:
        print("Track page / POST error:", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
