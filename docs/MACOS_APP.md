# Running as a macOS app (Dock icon)

You can run SoundBridge like a normal Mac app: double‑click **SoundBridge.app** to open a **native app window** (not the browser). The app icon stays in the Dock while the window is open; when you close the window or quit (Cmd+Q), the app exits.

**Requirements:** Install the desktop UI dependency so the app opens in a **native window** (not the browser) and stays in the Dock:

```bash
pip install pywebview
```

On macOS, `pyobjc-framework-Cocoa` is also used so the app appears in the Dock like a normal app. If pywebview is not installed, the .app will show an alert and will **not** open the browser.

## How to run

1. **From the project folder:** Double-click **SoundBridge.app** (in the same folder as `app.py`).  
   The app starts the server and opens a **native window** with the SoundBridge UI. The app icon remains in the Dock. Close the window or quit (Cmd+Q) to stop the server and remove the icon from the Dock.

2. **From anywhere:** Drag **SoundBridge.app** to Applications (or leave it on the Desktop). You must keep the **project folder** (the one that contains `app.py`, `config_shazam.py`, etc.) in a fixed place. The .app looks for that folder in the same parent directory as the .app:
   - If the .app is at `~/Applications/SoundBridge.app`, it will look for a folder `~/Applications/SoundBridge/` with `app.py` inside. So either:
     - Put the whole project folder next to the .app (e.g. `~/Applications/SoundBridge/` with the .app inside it), or  
     - Keep using the .app from inside the project (e.g. `.../SoundBridge/SoundBridge.app`).

   The launcher finds the project root as the folder that **contains** the .app (the folder can be named **MP3 Cleaner**, **SoundBridge**, or anything else). So the layout must be:
   ```
   SomeFolder/
     SoundBridge.app/
     (and the rest of the project: app.py, etc.)
   ```
   So "SomeFolder" = project root. If you moved only the .app to Applications, the launcher would look for the project in `Applications/` (sibling to the .app), which is wrong. So the recommended use is: **run the .app from inside the project folder** (e.g. double‑click `SoundBridge/SoundBridge.app`). Then the project root is `SoundBridge/`.

## App icon

The .app ships with a default icon (teal “bridge” style). To rebuild it or use your own:

1. **Use the script (recommended):** From the project root, run:
   ```bash
   python3 scripts/make_app_icon.py
   ```
   If there is a **`icon_1024.png`** (1024×1024) in the project root, it will be used. Otherwise a simple placeholder icon is generated and written to `SoundBridge.app/Contents/Resources/AppIcon.icns`.

2. **Manual .icns:** Create an `.icns` file (e.g. with [Image2Icon](https://apps.apple.com/app/image2icon/id892159703) or `sips`/`iconutil`), then:
   ```bash
   cp YourIcon.icns "SoundBridge.app/Contents/Resources/AppIcon.icns"
   ```

`Info.plist` already has `CFBundleIconFile` set to `AppIcon`. After changing the icon, restart the app (or log out/in) so the Dock picks it up.
