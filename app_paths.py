"""
Paths for app resources and writable data.
When running from a frozen bundle (e.g. py2app), config/caches/logs go to
~/Library/Application Support/SoundBridge/ so the bundle stays read-only.
"""
import os
import sys

APP_NAME = "SoundBridge"


def is_frozen() -> bool:
    """True when running from a py2app/PyInstaller-style bundle."""
    return getattr(sys, "frozen", False)


def get_app_support_dir() -> str:
    """
    Writable directory for config, caches, logs, and instance data.
    - Frozen: ~/Library/Application Support/SoundBridge/
    - Dev: same as project root (caller uses __file__ for dev).
    """
    if is_frozen():
        base = os.path.expanduser("~/Library/Application Support")
        path = os.path.join(base, APP_NAME)
        os.makedirs(path, exist_ok=True)
        return path
    return ""


def get_resource_root() -> str:
    """
    Root for read-only app resources (templates, static) inside the bundle.
    When frozen, py2app sets RESOURCEPATH to Contents/Resources.
    Returns '' when not frozen (caller should use __file__-based path).
    """
    if is_frozen():
        path = os.environ.get("RESOURCEPATH")
        if path:
            return path
        # Fallback: .../App.app/Contents/MacOS/python -> .../App.app/Contents/Resources
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(sys.executable))),
            "Contents",
            "Resources",
        )
    return ""


def get_project_root_for_data(__file__: str) -> str:
    """
    Root directory for writable data (config, caches, logs).
    Use from config_shazam, shazam_cache, or app.py by passing __file__.
    """
    if is_frozen():
        return get_app_support_dir()
    return os.path.dirname(os.path.abspath(__file__))
