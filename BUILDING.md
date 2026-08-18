# Building a standalone app

This packages the whole app (Python, Flask, the Google API libraries, and
the service account key) into something that runs on a machine with no
Python installed — double-click and go. Staff never see a terminal, install
a dependency, or touch a virtual environment.

**Important:** PyInstaller builds for the machine it runs on. It cannot
cross-compile — you can't build a Windows app from a Mac, or an Intel Mac
app from an Apple Silicon Mac. To support multiple platforms/architectures,
run the matching build script natively on each one.

| Target                  | Run this on                  | Script              |
|--------------------------|-------------------------------|----------------------|
| macOS Apple Silicon (M1/M2/M3/M4) | an Apple Silicon Mac | `./build_mac.sh`    |
| macOS Intel              | an Intel Mac                  | `./build_mac.sh`    |
| Windows                  | a Windows PC                  | `build_windows.bat` |

## macOS

```bash
./build_mac.sh
```

Produces `dist/ImpactChildcareWaitlist.app`. Zip it up to share:

```bash
cd dist && zip -r ImpactChildcareWaitlist-macOS.zip ImpactChildcareWaitlist.app
```

The architecture (Apple Silicon vs Intel) is whatever Mac you ran the
script on — check with `uname -m` (`arm64` = Apple Silicon, `x86_64` =
Intel). If you need both, build once on each kind of Mac.

**First launch on someone else's Mac:** since the app isn't signed with an
Apple Developer certificate, Gatekeeper will refuse to open it with a normal
double-click the first time. Right-click (or Control-click) the app →
**Open** → **Open** in the dialog. After that, it opens normally.

## Windows

On a Windows machine, with Python 3.9+ installed and on PATH:

```bat
build_windows.bat
```

Produces a folder at `dist\ImpactChildcareWaitlist\`. Share the **whole
folder** (zip it) — `ImpactChildcareWaitlist.exe` needs the other files
alongside it to run.

**First launch on someone else's PC:** Windows Defender SmartScreen may warn
that the app is from an "unrecognized publisher" (it isn't code-signed).
Click **More info** → **Run anyway**.

## What's bundled in

- The Flask app and all its Python dependencies (no `pip install` needed).
- The service account JSON key, so Sheets sync works out of the box.
- The `templates/` and `static/` folders (the UI).

## What's *not* bundled, and why

- **The database** (`waitlist.db`). It lives in a per-user app-data folder
  outside the app bundle itself:
  - macOS: `~/Library/Application Support/Impact Childcare Waitlist/`
  - Windows: `%APPDATA%\Impact Childcare Waitlist\`

  This matters because the packaged app re-extracts its bundled files to a
  fresh temporary folder on every launch — anything written inside the app
  bundle itself would vanish the moment the app closes. Keeping the database
  in a stable OS-standard location means data persists across launches, and
  each person running their own copy of the app gets their own local
  database (same as running from source).

## Rebuilding after a code change

Just re-run the same build script — it reuses the same virtual environment
and overwrites `build/` and `dist/`.

## Updating the bundled service account key

If the key file ever needs to change, replace the `.json` file in the
project root (see the README's Google Sheets setup section) — it's picked
up automatically the next time you build, by filename.
