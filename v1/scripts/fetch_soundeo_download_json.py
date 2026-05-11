#!/usr/bin/env python3
"""Fetch Soundeo /download/{id}/{format} response and write to soundeo_download_last_json.json for inspection."""
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

def main():
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import _get_soundeo_session

    cookies_path = get_soundeo_cookies_path()
    if not cookies_path or not os.path.exists(cookies_path):
        print("No Soundeo cookies file found. Log in via the app and Save Session first.")
        return 1

    track_page = "https://soundeo.com/track/egbert-straktrekken-original-mix-4560715.html"
    download_url = "https://soundeo.com/download/4560715/3"

    session = _get_soundeo_session(cookies_path)
    if not session:
        print("Could not build session from cookies.")
        return 1

    session.headers["Referer"] = track_page
    session.headers["X-Requested-With"] = "XMLHttpRequest"
    session.headers["Accept"] = "application/json, text/javascript, */*; q=0.01"

    print("GET", download_url)
    resp = session.get(download_url, timeout=15, allow_redirects=False)
    body = resp.content or b""
    ct = (resp.headers.get("Content-Type") or "").lower()

    out_path = os.path.join(_root, "soundeo_download_last_json.json")
    with open(out_path, "wb") as f:
        f.write(body)
    print("Wrote", len(body), "bytes to", out_path)
    print("Status:", resp.status_code, "Content-Type:", ct[:60])

    if "application/json" in ct and body:
        import json
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            print("Top-level keys:", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
            print("Preview:", (body.decode("utf-8", errors="replace") or "")[:400])
        except Exception as e:
            print("JSON parse:", e)
    return 0

if __name__ == "__main__":
    sys.exit(main())
