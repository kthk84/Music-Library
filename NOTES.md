# MP3 Cleaner – Notes

## Run the app

**Just run it yourself.** Use this command from the project folder:

```bash
python3 app.py
```

Or:

```bash
./run.sh
```

Then open **http://127.0.0.1:5002** in your browser.

## Undo dismiss (Sync tab)

Undo dismiss re-stars the track on Soundeo: it tries the HTTP API first, then falls back to the browser (same as the Star action) if the API doesn’t succeed (e.g. session/cookies). URL is resolved from status using key variants so the stored link is found.
