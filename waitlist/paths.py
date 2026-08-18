"""
Path resolution that works both running from source (`python run.py`) and
packaged as a standalone app (PyInstaller). The two need different answers:

- The SQLite database must live somewhere stable and writable. In a
  PyInstaller *onefile* build, the app is re-extracted to a fresh temp
  directory on every launch, so anything stored relative to the bundle
  itself would be wiped out and recreated empty every time. It has to live
  in a real per-user app-data folder instead.
- Bundled read-only resources (the service account key) are looked up
  relative to PyInstaller's extraction directory when frozen, and relative
  to the project root otherwise.
"""
import os
import sys


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def app_data_dir():
    """Directory for the SQLite database. Created if missing."""
    if is_frozen():
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        elif sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = os.path.join(base, "Impact Childcare Waitlist")
    else:
        basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        path = os.path.join(basedir, "instance")
    os.makedirs(path, exist_ok=True)
    return path


def bundled_resource_path(relative_path):
    """Path to a read-only file shipped alongside the code (e.g. the service
    account key)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, relative_path)
