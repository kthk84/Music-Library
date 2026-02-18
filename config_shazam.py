"""
Configuration for Shazam-Soundeo sync.
Stored in config.json (project root) so it persists with the project.
"""
import os
import json
from typing import List, Optional
from pathlib import Path

CONFIG_FILENAME = "config.json"
USER_CONFIG_PATH = os.path.expanduser("~/.mp3-cleaner-soundeo.json")

DEFAULT_CONFIG = {
    "destination_folders": [],
    "soundeo_cookies_path": "",
    "shazam_db_path": "",
    "headed_mode": True,  # True = visible browser for sync (session/cookies work more reliably)
    "stream_to_ui": True,
}


def _project_config_path() -> str:
    """Config in project root (next to app.py)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def _resolve_config_path() -> str:
    """Prefer project config so destination_folders persist with the project."""
    proj = _project_config_path()
    user = USER_CONFIG_PATH
    if os.path.exists(proj):
        return proj
    if os.path.exists(user):
        return user
    return proj


def _migrate_user_to_project_config() -> None:
    """If user config exists but project config doesn't, copy to project."""
    proj = _project_config_path()
    user = USER_CONFIG_PATH
    if os.path.exists(user) and not os.path.exists(proj):
        try:
            with open(user, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.makedirs(os.path.dirname(proj) or ".", exist_ok=True)
            with open(proj, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except (json.JSONDecodeError, IOError):
            pass


def load_config() -> dict:
    """Load config from disk. Returns default if missing."""
    _migrate_user_to_project_config()
    path = _resolve_config_path()
    if not os.path.exists(path):
        return _load_config_with_restore(dict(DEFAULT_CONFIG))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = dict(DEFAULT_CONFIG)
        out.update(data)
        return _load_config_with_restore(out)
    except (json.JSONDecodeError, IOError):
        return _load_config_with_restore(dict(DEFAULT_CONFIG))


def _load_config_with_restore(config: dict) -> dict:
    """Restore destination_folders from local_scan_cache if config has none."""
    folders = config.get("destination_folders") or []
    if folders:
        return config
    try:
        from shazam_cache import load_local_scan_cache
        cache = load_local_scan_cache()
        if cache and cache.get("folders"):
            config = dict(config)
            config["destination_folders"] = list(cache["folders"])
            save_config(config)
    except Exception:
        pass
    return config


def save_config(config: dict) -> None:
    """Save config to disk. Always use project config so it persists with the project."""
    path = _project_config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_destination_folders() -> List[str]:
    """Get list of folder paths to scan for local tracks. Paths are stripped; only existing dirs returned."""
    raw = load_config().get("destination_folders", []) or []
    return [p for p in (str(x).strip() for x in raw if x) if p and os.path.isdir(p)]


def get_destination_folders_raw() -> List[str]:
    """Return configured folder paths after stripping, without filtering by isdir (for error messages)."""
    raw = load_config().get("destination_folders", []) or []
    return [p for p in (str(x).strip() for x in raw if x) if p]


def get_soundeo_browser_profile_dir() -> str:
    """Path to persistent Chrome profile for Soundeo automation. Same profile for save-session and sync so login persists."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(project_dir, ".soundeo_browser_profile")
    os.makedirs(out, exist_ok=True)
    return out


def get_soundeo_cookies_path() -> str:
    """Path to saved Soundeo session cookies. Always in project dir so save and load use the same file."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(project_dir, "soundeo_cookies.json")
    # #region agent log
    try:
        import json as _json
        _log = {"location": "config_shazam.py:get_soundeo_cookies_path", "message": "cookies path resolved", "data": {"path": out, "project_dir": project_dir}, "timestamp": int(__import__("time").time() * 1000), "hypothesisId": "H5"}
        with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps(_log) + "\n")
    except Exception:
        pass
    # #endregion
    return out
