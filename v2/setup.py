"""
Build a standalone SoundBridge.app with py2app.
Run from the SoundBridge project root:
  pip install py2app
  python setup.py py2app

The resulting app is in dist/SoundBridge.app. Copy that .app to any Mac to run
without installing Python or pip. Config and caches are stored in
~/Library/Application Support/SoundBridge/.
"""
from setuptools import setup
import os

# Disable tkinter recipe: it imports _tkinter which loads Tcl/Tk and can abort
# with "macOS 26 (2602) or later required" on this system. We don't use tkinter.
try:
    import py2app.recipes.tkinter as tkrecipe
    _orig_check = tkrecipe.check
    def _no_tkinter_check(cmd, mf):
        return None  # Never run tkinter recipe
    tkrecipe.check = _no_tkinter_check
except Exception:
    pass

here = os.path.abspath(os.path.dirname(__file__))

# Data files to put inside the app bundle (templates and static for the web UI)
def resources_for_bundle():
    out = []
    for folder in ("templates", "static"):
        src = os.path.join(here, folder)
        if os.path.isdir(src):
            out.append(src)
    return out

OPTIONS = {
    "argv_emulation": False,
    "excludes": ["_tkinter", "tkinter"],  # Avoid tkinter recipe (Tcl/Tk version check fails on this system)
    "packages": [
        "flask",
        "werkzeug",
        "jinja2",
        "markupsafe",
        "mutagen",
        "requests",
        "selenium",
        "webdriver_manager",
        "PIL",
        "webview",  # import name is webview (pip package name is pywebview)
        "app",
        "config_shazam",
        "shazam_cache",
        "app_paths",
        "soundeo_automation",
    ],
    "includes": [
        "pyobjc",
        "AppKit",
        "Foundation",
        "WebKit",
        "objc",
    ],
    "resources": resources_for_bundle(),
    "plist": {
        "CFBundleName": "SoundBridge",
        "CFBundleDisplayName": "SoundBridge",
        "CFBundleIdentifier": "com.soundbridge.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="SoundBridge",
    app=["launch_desktop.py"],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
