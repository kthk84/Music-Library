"""
Configuration for Shazam-Soundeo sync.
Stored in config.json (project root).
"""
import os
import json
import re
from typing import List, Optional, Dict, Any

CONFIG_FILENAME = "config.json"
USER_CONFIG_PATH = os.path.expanduser("~/.mp3-cleaner-soundeo.json")

DEFAULT_CONFIG = {
    "destination_folders": [],
    "soundeo_cookies_path": "",
    "shazam_db_path": "",
    "headed_mode": True,
    "stream_to_ui": True,
    # Search all: False = browser (Selenium), True = HTTP (no Chrome). Use True if browser path fails.
    "search_all_use_http": False,
    # --- Soundeo browser: one of two modes ---
    # "attach" = connect to Chrome already running with --remote-debugging-port=9222
    # "launch" = start Chrome with a persistent profile (cookies/login persist; no temp profile)
    "soundeo_browser_mode": "launch",
    "soundeo_debugger_address": "127.0.0.1:9222",
    # For mode "launch": persistent user-data-dir (and optional profile name).
    # Leave user_data_dir empty to use app profile (.soundeo_browser_profile).
    # Chrome profile path by OS:
    #   Mac:     ~/Library/Application Support/Google/Chrome
    #   Windows: %LOCALAPPDATA%\\Google\\Chrome\\User Data
    #   Linux:   ~/.config/google-chrome
    "soundeo_chrome_user_data_dir": "",
    "soundeo_chrome_profile_directory": "",
    # Soundeo download: one of destination_folders where AIFF downloads are saved (only one active).
    "soundeo_download_folder": "",
}


def _project_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def _resolve_config_path() -> str:
    proj = _project_config_path()
    user = USER_CONFIG_PATH
    if os.path.exists(proj):
        return proj
    if os.path.exists(user):
        return user
    return proj


def _migrate_user_to_project_config() -> None:
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
    _migrate_user_to_project_config()
    path = _resolve_config_path()
    if not os.path.exists(path):
        return _load_config_with_restore(dict(DEFAULT_CONFIG))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = dict(DEFAULT_CONFIG)
        out.update(data)
        # Migrate old keys to new single-mode model
        if out.get("soundeo_use_running_chrome") and "soundeo_browser_mode" not in data:
            out["soundeo_browser_mode"] = "attach"
        if out.get("soundeo_chrome_debugger_address") and not out.get("soundeo_debugger_address"):
            out["soundeo_debugger_address"] = out["soundeo_chrome_debugger_address"]
        return _load_config_with_restore(out)
    except (json.JSONDecodeError, IOError):
        return _load_config_with_restore(dict(DEFAULT_CONFIG))


def _load_config_with_restore(config: dict) -> dict:
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
    path = _project_config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_destination_folders() -> List[str]:
    raw = load_config().get("destination_folders", []) or []
    return [p for p in (str(x).strip() for x in raw if x) if p and os.path.isdir(p)]


def get_destination_folders_raw() -> List[str]:
    raw = load_config().get("destination_folders", []) or []
    return [p for p in (str(x).strip() for x in raw if x) if p]


def get_soundeo_download_folder() -> str:
    """Folder path where Soundeo AIFF downloads are saved. Must be one of destination_folders."""
    path = (load_config().get("soundeo_download_folder") or "").strip()
    return path


def set_soundeo_download_folder(folder_path: str) -> None:
    """Set the single Soundeo download folder (one of destination_folders). Pass '' to clear."""
    cfg = load_config()
    cfg = dict(cfg)
    cfg["soundeo_download_folder"] = (folder_path or "").strip()
    save_config(cfg)


# --- Soundeo browser: single source of truth ---

def _default_chrome_user_data_dir() -> str:
    if os.name == "posix":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    return os.path.expanduser("~")


def _resolve_profile_display_name(user_data_dir: str, name: str) -> str:
    """Resolve display name (e.g. 'testh') to folder name (e.g. 'Profile 1') via Chrome Local State."""
    name = (name or "").strip()
    if not name or re.match(r"^Default$", name, re.I) or re.match(r"^Profile\s*\d+$", name, re.I):
        return name
    path = os.path.join(user_data_dir, "Local State")
    if not os.path.isfile(path):
        return name
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        for folder_name, info in ((data.get("profile") or {}).get("info_cache") or {}).items():
            if isinstance(info, dict) and (info.get("name") or "").strip().lower() == name.lower():
                return folder_name
    except Exception:
        pass
    return name


def get_soundeo_browser_config() -> Dict[str, Any]:
    """
    Returns the effective Soundeo browser config. One place for all driver creation.
    - mode: "attach" | "launch"
    - debugger_address: for attach (e.g. "127.0.0.1:9222")
    - user_data_dir: for launch (path to Chrome User Data dir)
    - profile_directory: for launch (e.g. "Profile 1" or None for default)
    """
    cfg = load_config()
    mode = (cfg.get("soundeo_browser_mode") or "launch").strip().lower()
    if mode == "attach":
        addr = (cfg.get("soundeo_debugger_address") or "127.0.0.1:9222").strip()
        if addr and ":" not in addr:
            addr = "127.0.0.1:" + addr
        return {"mode": "attach", "debugger_address": addr or "127.0.0.1:9222"}
    # launch
    raw_dir = (cfg.get("soundeo_chrome_user_data_dir") or "").strip()
    raw_profile = (cfg.get("soundeo_chrome_profile_directory") or "").strip()
    if raw_dir:
        user_data_dir = os.path.abspath(os.path.expanduser(raw_dir))
    elif raw_profile:
        user_data_dir = _default_chrome_user_data_dir()
    else:
        project_dir = os.path.dirname(os.path.abspath(__file__))
        user_data_dir = os.path.join(project_dir, ".soundeo_browser_profile")
        os.makedirs(user_data_dir, exist_ok=True)
    profile_directory = _resolve_profile_display_name(user_data_dir, raw_profile) if raw_profile else None
    return {"mode": "launch", "user_data_dir": user_data_dir, "profile_directory": profile_directory}


def get_soundeo_cookies_path() -> str:
    """Path to cookie file (for optional save/load)."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(project_dir, "soundeo_cookies.json")
