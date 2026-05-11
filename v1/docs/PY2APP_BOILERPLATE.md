# py2app standalone build: boilerplate and checklist

Use this when building a **standalone macOS .app** with py2app (Flask + pywebview or similar). It documents the fixes that make the built app actually standalone (no “install with pip” dialog).

---

## Quick build (after setup is done)

```bash
cd /path/to/YourProject
pip3 install py2app
python3 setup.py py2app
```

**Run the built app from `dist/YourApp.app`**, not from any .app in the project folder (see “Two different .app bundles” below).

---

## 1. setup.py: critical options

### Use `resources`, not `datas`

py2app expects the key **`resources`** (a list of file/folder paths to copy into the bundle). Older docs sometimes say `datas`; that will error: `command 'py2app' has no such option 'datas'`.

```python
# Data files (templates, static, etc.) into the app bundle
def resources_for_bundle():
    out = []
    for folder in ("templates", "static"):  # adapt to your project
        src = os.path.join(here, folder)
        if os.path.isdir(src):
            out.append(src)
    return out

OPTIONS = {
    # ...
    "resources": resources_for_bundle(),  # not "datas"
}
```

### Disable the tkinter recipe (avoid Tcl/Tk crash)

If you don’t use tkinter, disable py2app’s tkinter recipe. Otherwise it can **import _tkinter** and trigger a Tcl/Tk version check that aborts with something like:  
`macOS 26 (2602) or later required, have instead 16 (1602) !`

Add this at the top of `setup.py`, before defining OPTIONS:

```python
# Disable tkinter recipe: it imports _tkinter which loads Tcl/Tk and can abort.
try:
    import py2app.recipes.tkinter as tkrecipe
    def _no_tkinter_check(cmd, mf):
        return None
    tkrecipe.check = _no_tkinter_check
except Exception:
    pass
```

Also add to OPTIONS:

```python
"excludes": ["_tkinter", "tkinter"],
```

### Package names: use the **import** name

List packages by the name you **import**, not the pip package name. Example: the pip package is `pywebview`, but you `import webview`, so list **`"webview"`** in `packages`. Otherwise py2app can fail with `ImportError: No module named 'pywebview'` when resolving bootstraps.

```python
"packages": [
    # ...
    "webview",   # import name (pip package is pywebview)
    "flask",
    # ...
],
```

### Minimal setup() example

```python
setup(
    name="YourApp",
    app=["launch_desktop.py"],   # or your main script
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
```

---

## 2. Launch script: find bundled packages when frozen

When the app runs as a **frozen** py2app bundle, `sys.path` may not yet include the app’s `lib` folder, so `import webview` (or other bundled packages) can fail and the app shows “install with pip”. Fix: at the very start of your main launch script (e.g. `launch_desktop.py`), add:

```python
import sys
import os

# When running as a frozen py2app bundle, ensure the app's lib is on sys.path
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    base = os.environ.get("RESOURCEPATH", "")
    if base:
        lib = os.path.join(base, "lib", "python%d.%d" % sys.version_info[:2])
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
```

Do this **before** any `import webview` (or other bundled packages). Then the standalone .app will use its own bundled dependencies.

---

## 3. Two different .app bundles

| Location | What it is | Use it when |
|----------|------------|--------------|
| **`dist/YourApp.app`** | Built by py2app; contains Python + all deps. | **Standalone:** copy this to another Mac, no pip. |
| **`YourApp.app`** in project root | Often a shell script that runs `python3 launch_desktop.py` with **system** Python. | Dev only; needs pip install on that machine. |

Always run the **dist** .app when you want the “no pip” experience. To avoid confusion, you can rename the dev .app (e.g. `YourApp-Dev.app`).

---

## 4. Dependencies before building

Install everything your app needs (and that you list in `setup.py`) before running py2app, so the bundler can find them:

```bash
pip3 install -r requirements.txt
pip3 install pyobjc   # if using pywebview on macOS
pip3 install py2app
python3 setup.py py2app
```

---

## 5. Checklist for a new project

- [ ] `setup.py` uses **`resources`** (not `datas`) for data files.
- [ ] Tkinter recipe disabled in `setup.py` if you don’t use tkinter (patch + `excludes`).
- [ ] **Packages** list uses **import names** (e.g. `webview` not `pywebview`).
- [ ] Main launch script adds bundle **lib** to `sys.path` when `sys.frozen` (before importing bundled packages).
- [ ] Build: `python3 setup.py py2app`.
- [ ] Run **`dist/YourApp.app`** to test; don’t rely on a dev .app in the project folder.

---

## 6. Where this was proven

SoundBridge uses this pattern: `setup.py` and `launch_desktop.py` in the repo are the reference. See also `docs/STANDALONE_BUILD.md` for user-facing “portable vs standalone” and “copy dist .app to another Mac” instructions.
