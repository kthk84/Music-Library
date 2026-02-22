#!/usr/bin/env python3
"""
Launch SoundBridge in a native app window (macOS/Windows/Linux) instead of the browser.
Starts the Flask server in a background thread, then opens a pywebview window.
The app stays in the Dock (macOS) and feels like a normal desktop app.
"""
import sys
import os
import threading
import time
import urllib.request

# When running as a frozen py2app bundle, ensure the app's lib is on sys.path
# so the bundled webview (and other deps) are found. Otherwise "import webview" fails
# and the app asks for pip install.
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    base = os.environ.get("RESOURCEPATH", "")
    if base:
        lib = os.path.join(base, "lib", "python%d.%d" % sys.version_info[:2])
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)

# Start Flask in a daemon thread so it dies when we exit
def run_server():
    import app as app_module
    app_module.app.run(debug=False, port=5002, host='127.0.0.1', threaded=True, use_reloader=False)

def wait_for_server(url='http://127.0.0.1:5002/', timeout=15, interval=0.3):
    for _ in range(int(timeout / interval)):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(interval)
    return False


def _macos_activate_app():
    """Set macOS activation policy so the app appears in the Dock and can be activated like a normal app."""
    try:
        from AppKit import NSApplication, NSApp
        app = NSApplication.sharedApplication()
        # NSApplicationActivationPolicyRegular = 0: show in Dock, has menu bar
        app.setActivationPolicy_(0)
        app.activateIgnoringOtherApps_(True)
    except Exception:
        try:
            from Cocoa import NSApplication, NSApp
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(0)
            app.activateIgnoringOtherApps_(True)
        except Exception:
            pass


def main():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    if not wait_for_server():
        print('SoundBridge: server did not start in time.', file=sys.stderr)
        sys.exit(1)

    try:
        import webview
    except ImportError:
        print('SoundBridge: pywebview not installed. Install with: pip install pywebview', file=sys.stderr)
        if sys.platform == 'darwin':
            import subprocess
            subprocess.run([
                'osascript', '-e',
                'display alert "SoundBridge" message "Pywebview is required for the app window. Install it with: pip install pywebview"'
            ], check=False)
            server_thread.join()
        sys.exit(1)

    # Single window: no browser chrome, normal title bar so it feels like an app
    window = webview.create_window(
        'SoundBridge',
        'http://127.0.0.1:5002',
        width=1200,
        height=800,
        min_size=(800, 600),
        resizable=True,
    )

    def on_gui_started():
        if sys.platform == 'darwin':
            _macos_activate_app()

    # debug=False so the window does not show any browser-like dev tools or toolbar
    webview.start(on_gui_started, debug=False)
    sys.exit(0)


if __name__ == '__main__':
    main()
