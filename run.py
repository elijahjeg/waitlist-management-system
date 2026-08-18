"""
Impact Childcare — Waitlist Manager
Run locally with:  python run.py
Then open http://127.0.0.1:5000 in your browser.
"""
import json
import os
import sys
import threading
import urllib.request
import webbrowser

from waitlist import create_app
from waitlist.paths import is_frozen
from waitlist.sheets_sync import sync_from_sheet, sync_archive_from_sheet

PORT = 5000
APP_URL = f"http://127.0.0.1:{PORT}"

app = create_app()


def _already_running():
    """True if a real instance of this app (not some unrelated service
    squatting on the port -- e.g. macOS AirPlay Receiver also listens on
    5000) is already serving requests on PORT."""
    try:
        with urllib.request.urlopen(f"{APP_URL}/api/bootstrap", timeout=0.5) as resp:
            return "entries" in json.loads(resp.read())
    except Exception:
        return False


def _run_startup_sync():
    with app.app_context():
        result = sync_from_sheet()
        if result.get("ok"):
            app.logger.info(
                "Google Sheets sync: %s new, %s already on file, %s removed (no longer in the sheet)",
                result["added"], result["skipped"], result["removed"],
            )
        else:
            app.logger.warning("Google Sheets sync skipped: %s", result.get("error"))

        archive_result = sync_archive_from_sheet()
        if archive_result.get("ok"):
            app.logger.info(
                "Archive sheet sync: %s new, %s already on file, %s removed (no longer in the archive sheet)",
                archive_result["added"], archive_result["skipped"], archive_result["removed"],
            )
        elif archive_result.get("error") != "No archive sheet configured.":
            app.logger.warning("Archive sheet sync skipped: %s", archive_result.get("error"))


if __name__ == "__main__":
    if is_frozen():
        if _already_running():
            # Launched a second time while already running (e.g.
            # double-clicking the packaged app again) -- just open the
            # existing instance instead of failing to bind the port again.
            webbrowser.open(APP_URL)
            sys.exit(0)

        # Packaged app: no debug reloader (it re-execs the process, which
        # doesn't work once bundled), and no terminal for the user to read
        # a URL from -- open the browser automatically instead.
        _run_startup_sync()
        threading.Timer(1.0, lambda: webbrowser.open(APP_URL)).start()
        app.run(debug=False, port=PORT)
    else:
        # Under the debug reloader, run.py executes twice (a watcher
        # process, then the process that actually serves requests) --
        # WERKZEUG_RUN_MAIN is only set in the latter, so this runs the
        # startup sync exactly once.
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            _run_startup_sync()
        app.run(debug=True, port=PORT)
