# Running SoundBridge on another Mac (portable vs standalone)

## If you just copy the project folder to a new Mac

**The installed Python packages are not included.**  
`pip install -r requirements.txt` installs into your **system or user** Python (e.g. `~/Library/Python/3.9/...` or `/Library/Python/3.9/...`), not into the SoundBridge folder. So when you copy the folder to a new Mac you get:

- All app code, `SoundBridge.app`, `templates/`, `static/`, `config.json`, etc.
- **No** pywebview, Flask, Selenium, or other pip dependencies.

On the new Mac you must run once:

```bash
cd /path/to/SoundBridge
pip3 install -r requirements.txt
```

After that, double‑click `SoundBridge.app` (or run `python3 launch_desktop.py`) and it will work. So: **one-time install per machine**, then the folder is portable.

---

## Making it fully standalone (no pip on the other Mac)

To run on a new Mac **without** installing Python or running pip, you need a **standalone .app** that bundles Python and all dependencies inside the app.

### Option 1: Build with py2app (recommended)

1. On your **current** Mac (with Python and deps already installed), install py2app and build:

   ```bash
   cd /path/to/SoundBridge
   pip3 install py2app
   python3 setup.py py2app
   ```

2. The standalone app is created at **`dist/SoundBridge.app`**.

3. **Run the app from `dist/SoundBridge.app`** (the one inside the `dist` folder). Do not use the `SoundBridge.app` in the project root—that one is a dev launcher that uses system Python and will ask for `pip install pywebview`.

4. Copy **`dist/SoundBridge.app`** to the other Mac (e.g. drag to Applications or the Desktop).

5. On the new Mac, double‑click that .app. No Python or pip needed.

**Note:** The first time you run the standalone app on a new Mac, config and data (e.g. destination folders, skip list) are stored in **`~/Library/Application Support/SoundBridge/`**, not next to the .app. So you may need to reconfigure folders and Soundeo connection once on that machine.

**For maintainers:** Build gotchas, setup.py options, and a reusable checklist for similar apps are in **[docs/PY2APP_BOILERPLATE.md](PY2APP_BOILERPLATE.md)**.

### Option 2: Use a venv inside the project folder (semi‑portable)

You can put a virtualenv **inside** the SoundBridge folder and point the launcher at it, so the folder contains its own Python and packages:

1. In the project folder:

   ```bash
   cd /path/to/SoundBridge
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

2. Change `SoundBridge.app` so it runs `./venv/bin/python launch_desktop.py` instead of `python3 launch_desktop.py` (see below).

3. Copy the **entire** SoundBridge folder (including `venv/`) to the other Mac.

4. On the new Mac, that machine still needs **Python 3** installed (to run `venv/bin/python`). So this is “no pip install on the other Mac” but **not** “no Python at all”. For true zero-install, use the py2app build (Option 1).

---

## Summary

| What you do | On a new Mac |
|-------------|---------------|
| Copy project folder only | Run `pip3 install -r requirements.txt` once, then use `SoundBridge.app`. |
| Copy **`dist/SoundBridge.app`** (after `python3 setup.py py2app`) | Double‑click the .app; no Python or pip needed. |
| Copy project folder + venv and launcher that uses `venv/bin/python` | Need Python 3 on the new Mac; no pip install there. |

So: **“Include it in the package so it can run standalone”** = build the app with py2app and copy **`dist/SoundBridge.app`** to the other Mac. The `setup.py` in the project root is there for that.
