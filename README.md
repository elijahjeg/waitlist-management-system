# Impact Childcare — Waitlist Manager (local version)

A local, runnable version of the waitlist draft: upcoming openings from the Lillio
enrollment export, a chronological waitlist/call list per age group, an archive with
the 3-year retention rule, and adjustable age-group cutoffs. Same look and features
as the in-chat draft, now backed by a real local database (SQLite) instead of
browser storage.

## Requirements

- Python 3.9 or newer

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open **http://127.0.0.1:5000** in your browser.

A SQLite database is created automatically at `instance/waitlist.db` on first run —
that's where all your data lives between sessions. Delete that file any time to start
completely fresh.

## Waitlist form responses sync automatically

New submissions to the waitlist Google Form are pulled in straight from the linked
Google Sheet — once automatically every time you run `python run.py`, and any time
via the **Sync form responses** button on the **Waitlist / Call list** tab. There's
no more CSV export/upload step for this.

This needs a Google Cloud **service account**:

1. In the [Google Cloud Console](https://console.cloud.google.com), create/select a
   project and enable the **Google Sheets API**.
2. **IAM & Admin → Service Accounts → Create Service Account** (any name works).
3. On that service account: **Keys → Add Key → Create new key → JSON**. This
   downloads a credentials file — save it in the project's root folder.
4. Open the Google Sheet the Form writes to → **Share** → paste in the service
   account's email (looks like `...@your-project.iam.gserviceaccount.com`) with
   **Editor** access. (Editor, not Viewer — archiving a family deletes its row
   from this sheet, which needs write access.)
5. The app comes pointed at Impact Childcare's current Sheet/tab by default.
   If the Form ever moves to a new Sheet or tab, **no code changes are
   needed** — go to the **Settings** tab → **Waitlist form sheet**, paste the
   new sheet's full URL (address bar, including the `gid=...`), and click
   Save. Remember to share the new sheet with the service account first.

   For advanced use (e.g. a different key file location, or overriding
   without touching the database), environment variables take priority over
   the hardcoded default but are overridden by whatever's pasted into
   Settings:
   ```bash
   export GOOGLE_SHEET_ID="your-sheet-id"       # from the sheet's URL
   export GOOGLE_SHEET_GID="your-tab-gid"        # the gid= in the URL, for a specific tab
   export GOOGLE_SERVICE_ACCOUNT_FILE="/path/to/key.json"
   ```

If the key file is missing or the sync fails (e.g. no internet, sheet not shared
with the service account), the app logs a warning and starts normally — it never
blocks startup. Rows already on file (matched by child name + submission
timestamp) are skipped, so re-syncing is always safe.

**Never commit the service account JSON key** — it grants read/write access to
the sheet. `.gitignore` already excludes `*.json` files in the project root.

### Sync also removes families whose row disappeared from the sheet

If a row is deleted (or its child name/timestamp edited) directly in the
sheet — outside this app — the next sync removes that family here too,
keeping the two in sync in both directions. This **permanently deletes** the
local record, including any notes/status history, so it's worth knowing
exactly when it applies:

- Only families the app has confirmed came from a sheet row are eligible.
  Families added via **Add family** in this app are never touched, since
  they were never expected to be in the sheet.
- **Archived** families are never touched either, regardless of what's in
  the sheet — archiving already intentionally removes the row (see below),
  so its continued absence is expected, not a sign it should be deleted here.
- Entries already in your database from before this behavior existed aren't
  deleted just because they don't match the current sheet — the very first
  sync after upgrading only starts tracking "confirmed from the sheet"
  going forward, so nothing gets removed based on a guess about older data.

Removed families are named in the sync result (toast on manual sync, log
line on startup) so it's never silent.

### Families added with "Add family" are pushed to the sheet too

Adding a family directly in the app (rather than through the Form) pushes
their row into the waitlist sheet right away, and from then on they're
treated exactly like a Form submission — including by the deletion rule
above. This is what makes the app usable from **multiple devices without
clashes**: the Google Sheet is the shared source of truth, so a family added
on one computer shows up for everyone else on their next sync, instead of
being stuck in that one device's local database forever.

If the push fails (offline, sheet unreachable), the family is still saved
locally right away — you'll see a warning, but nothing is lost. The next
successful sync (manual or automatic on startup) retries the push
automatically, so it doesn't require you to notice and retry by hand.

## Archiving moves the family between the two sheets

When you click **Archive family**:

1. The family's row is **permanently deleted** from the waitlist form
   responses sheet (matched by child name + submission timestamp). This keeps
   the live sheet showing only families still actively waiting.
2. If an **archive sheet** is configured (Settings tab → "Archive sheet"),
   the family's row is appended there — same columns as the form sheet (see
   below), colored by status the same way as the source sheet. This is
   optional; skip it and archived families still stay in this app (Archive
   tab, CSV export) either way.

Clicking **Restore to active list** does the exact reverse: the row is
removed from the archive sheet and added back to the waitlist form sheet,
colored by its current status.

The archive sheet needs the same service account shared with **Editor**
access. If any step fails (offline, sheet not shared, row already removed/
moved), the local archive/restore still goes through and you'll see a
warning toast explaining what didn't sync — it never blocks the action
itself.

Row deletion is **not undoable from within this app** — recovery would mean
using Google Sheets' own version history (File → Version history) on that
sheet.

### The archive sheet uses the same columns as the form sheet

So a row can move cleanly in either direction, the archive sheet's columns
match the waitlist form's responses sheet exactly — Timestamp, Parent's
name, Phone Number, Email Address, Child's Name, Gender, Child's Birthday,
Preferred Enrolment Date, Do you require subsidy?, Do you have any questions
for us?, Email Address (yes, twice — matching the form's own layout),
What classroom would you like to be waitlisted for?, Notes. A header row is
written automatically the first time something is archived to an empty
sheet. This is a different, leaner format than the CSV export (Settings →
Archive tab → "Export archive (CSV)"), which keeps richer detail (final
status, both dates) since it's a standalone record rather than something
meant to move back into a form sheet.

### The archive sheet syncs like the waitlist sheet does

Same idea as the waitlist form sync, just for the archive sheet:

- **Sync archive sheet** button (Archive tab), plus automatic sync on every
  startup — pulls in any archived family that's in the archive sheet but not
  yet known locally (e.g. someone added a row there directly, or you're
  running this on a fresh machine and want the full archive history).
- Removes local archived records whose row disappeared from the archive
  sheet — same safety rule as the waitlist sync: only entries this app has
  confirmed came from the archive sheet are ever auto-deleted, and it's
  always named in the result so nothing vanishes silently.

## Status tag colors sync to the sheet(s) too

Each family's status tag in this app (Not yet contacted, Potential spot,
Special note, In communication, Offered, Confirmed) has a color — the same
color is applied as the row's background in the **source sheet** the moment
you change a status, and again on the row added to the **archive sheet** when
a family is archived. So a row colored green in either sheet means
"Confirmed," matching what you'd see in the app, without opening it.

This only affects the source sheet while a family is still active — once
archived, their row is removed from the source sheet entirely (see above),
so the color naturally only persists on the archive-sheet copy from then on.
Like the rest of the sheet sync, a failure here (offline, row not found) just
shows a warning toast — it never blocks the status change itself.

### A note on speed

Changing a status, archiving, restoring, or adding a family all talk to
Google Sheets in the background, which can take a few seconds — slower than
everything else in this app, which is instant since it's all local. During
that wait, the relevant modal/button shows a spinner and "Talking to Google
Sheets…" so it's clear something is happening rather than the UI looking
stuck.

## Try it with the sample data

1. Go to the **Openings** tab → **Import Lillio export** → choose
   `sample_data/sample_lillio_enrollment_export.csv`

You should see upcoming graduations sorted by date and a September-openings banner.
The waitlist itself now comes from the live Google Sheet sync above rather than a
sample CSV — once synced, you can filter it by age group, click into a family,
change status colors, add notes, and archive.

## Project layout

```
run.py                    entry point — `python run.py`
waitlist/
  __init__.py              Flask app factory + DB setup
  paths.py                 DB/resource path resolution (dev vs. packaged app)
  models.py                SQLAlchemy models (WaitlistEntry, RosterEntry, Bracket, Settings)
  routes.py                All /api/* endpoints
  sheets_sync.py           Google Sheets sync + archive/restore/delete-row logic
  utils.py                 CSV column matching, date parsing, age/bracket logic
  templates/index.html     Page shell
  static/js/app.js          All frontend logic (vanilla JS, no build step)
sample_data/               Two CSVs to try the app with
instance/                  Created automatically — holds waitlist.db (not in git)
waitlist.spec              PyInstaller build spec (see BUILDING.md)
build_mac.sh, build_windows.bat   Standalone-app build scripts (see BUILDING.md)
```

## Running it as a standalone app (no Python needed)

For non-technical staff, this can be packaged into a double-clickable app —
no terminal, no `pip install`. See **[BUILDING.md](BUILDING.md)** for how to
build it for macOS (Apple Silicon or Intel) and Windows.

## Notes

- **Openings are computed, not read from Lillio.** Lillio's export has a "Grad
  date" column, but it isn't something staff keep up to date — so this app
  ignores it entirely and instead computes when each enrolled child ages out of
  their current group from their date of birth against the age-group cutoffs in
  Settings. If Lillio's own Classroom field hasn't been updated to match a
  child's current age, the Openings tab flags that too. Children older than
  every configured age group show up in a "needs attention" notice rather than
  a nonsense negative day count.
- **Internet is needed** the first time the page loads (Tailwind CSS and the Lucide
  icons load from a CDN in `templates/index.html`), and each time the app syncs
  waitlist form responses from Google Sheets. Everything else — the server, the
  database, the Lillio roster import — runs fully locally.
- **Age-group cutoffs** (Infant/Toddler/Preschool/Pre-K, in months) are placeholders
  based on typical daycare bands. Confirm the real cutoffs with Erin, then update
  them once in the **Settings** tab.
- **Syncing waitlist responses** (automatic on startup, or via the button) merges in
  only new rows (matched by child name + timestamp) — it won't wipe out statuses or
  notes you've already set on existing families.
- **Re-importing the Lillio roster CSV** fully replaces the roster each time, matching
  the "stateless, re-parse fresh" approach from the original architecture notes.
- **"Clear all data"** in Settings wipes the whole database. It's meant for
  demos/testing, not for day-to-day use.
- This is still a **single-user local tool** — there's no login, and everyone who
  runs it locally has their own separate database that pulls from the same shared
  Google Sheet.

## Why Flask instead of Django

Django's strengths — multiple user accounts, built-in permission levels, an admin
site — matter most once this needs several staff members logging in with different
access levels (one of the open questions from the original handoff). For a single-
user local draft, Flask gets you running with a lot less scaffolding. The models in
`models.py` map cleanly onto Django models if/when that becomes worth doing.

## Known limitations of this draft

- No authentication — anyone with access to the machine running it can open
  `http://127.0.0.1:5000` and see/edit everything.
- No multi-device sync of edits — each machine has its own database of statuses/
  notes; only the incoming form responses are shared, via the Google Sheet.
- Minimal validation on imported/synced data — unrecognized columns are ignored
  rather than flagged, so double-check if a sync or import adds fewer rows than
  expected.
