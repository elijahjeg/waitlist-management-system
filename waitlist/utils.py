"""
Helpers for flexible CSV column matching and date/age math.

Column names in real-world exports (Google Forms, Lillio) vary in punctuation and
wording, so headers are matched loosely rather than requiring an exact string.
"""
import re
import json
import calendar
from datetime import datetime, date



# ---------------------------------------------------------------------------
# Header matching
# ---------------------------------------------------------------------------

WAITLIST_FIELD_PATTERNS = [
    ("timestamp", ["timestamp"]),
    ("parentName", ["parents name", "parent name"]),
    ("phone", ["phone number", "phone"]),
    ("childName", ["childs name", "child name"]),
    ("gender", ["gender"]),
    ("dob", ["childs birthday", "child birthday", "date of birth", "birthday", "dob"]),
    ("preferredDate", ["preferred enrolment date", "preferred enrollment date", "preferred start date"]),
    ("subsidy", ["do you require subsidy", "require subsidy", "subsidy"]),
    ("questions", ["do you have any questions for us", "questions for us", "questions"]),
    ("email", ["email address", "email"]),
    ("requestedClassroom", [
        "what classroom would you like to be waitlisted for",
        "classroom would you like",
        "requested classroom",
        "classroom",
    ]),
    ("notes", ["notes", "note"]),
]

ROSTER_FIELD_PATTERNS = [
    ("firstName", ["first name"]),
    ("lastName", ["last name"]),
    ("dob", ["dob", "date of birth"]),
    ("age", ["age"]),
    ("classroom", ["classroom"]),
    ("status", ["status"]),
    ("enrollDate", ["enroll date", "enrollment date"]),
    ("rotation", ["rotation"]),
    ("schedule", ["schedule"]),
]
# Note: Lillio's export has a "Grad date" column, but it isn't a field staff keep
# up to date -- the real signal for when a spot opens is the child's age against
# the age-group cutoffs, so that column is intentionally not imported. See
# `add_months` below, used to compute when each child ages out of their bracket.


def norm_header(h):
    h = (h or "").lower()
    h = h.replace("\u2019", "'").replace("\u2018", "'")
    h = h.replace("'", "")
    h = re.sub(r"[^a-z0-9]+", " ", h)
    return h.strip()


def build_field_map(raw_headers, field_patterns):
    """Map each raw CSV header to one of our internal field keys, best-match, one use each."""
    mapping = {}
    used = set()
    for raw in raw_headers or []:
        norm = norm_header(raw)
        if not norm:
            continue
        best_key, best_score = None, -1
        for key, patterns in field_patterns:
            if key in used:
                continue
            for p in patterns:
                if norm == p:
                    score = 1000
                elif norm in p or p in norm:
                    score = len(p)
                else:
                    continue
                if score > best_score:
                    best_score, best_key = score, key
        if best_key:
            mapping[raw] = best_key
            used.add(best_key)
    return mapping


# ---------------------------------------------------------------------------
# Dates and ages
# ---------------------------------------------------------------------------

_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%B %d, %Y"]


def parse_date(value):
    """Best-effort parse of a date that may come with a time component attached."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Timestamps often look like "7/31/2026 10:39:37" — try just the date part.
    head = s.split(" ")[0]
    if head != s:
        for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"]:
            try:
                return datetime.strptime(head, fmt).date()
            except ValueError:
                continue
    return None


def age_in_months(dob, as_of=None):
    if not dob:
        return None
    as_of = as_of or date.today()
    months = (as_of.year - dob.year) * 12 + (as_of.month - dob.month)
    if as_of.day < dob.day:
        months -= 1
    return max(0, months)


def add_months(d, months):
    """Add a whole number of calendar months to a date, clamping the day if the
    target month is shorter (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def classify_bracket(months, brackets):
    if months is None:
        return None
    for b in sorted(brackets, key=lambda x: x["minMonths"]):
        if b["minMonths"] <= months <= b["maxMonths"]:
            return b
    return None


def loose_match(a, b):
    if not a or not b:
        return True
    a, b = a.lower(), b.lower()
    return a in b or b in a


def dedupe_key(rec):
    child = (rec.get("childName") or "").strip().lower()
    ts = (rec.get("timestamp") or rec.get("dob") or "").strip()
    return f"{child}|{ts}"


def new_note(text):
    return {"id": f"note_{datetime.now().timestamp()}", "ts": datetime.now().isoformat(), "text": text}


# ---------------------------------------------------------------------------
# Archive record formatting -- shared by the CSV export and the archive-sheet sync
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    "none": "Not yet contacted",
    "purple": "Potential spot",
    "blue": "Special note",
    "yellow": "In communication",
    "red": "Offered - no response / declined",
    "green": "Confirmed",
}

# Same palette as the status pills in the UI (Tailwind's *-100 background
# shades), so a row's color in the Sheet matches its tag color in the app.
STATUS_COLORS_HEX = {
    "none": "#f5f5f4",    # stone-100
    "purple": "#f3e8ff",  # purple-100
    "blue": "#dbeafe",    # blue-100
    "yellow": "#fef9c3",  # yellow-100
    "red": "#fee2e2",     # red-100
    "green": "#dcfce7",   # green-100
}


def hex_to_rgb_float(hex_color):
    """'#rrggbb' -> {"red": 0-1, "green": 0-1, "blue": 0-1}, the format the
    Sheets API expects for cell background colors."""
    h = hex_color.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }

ARCHIVE_HEADERS = [
    "Child Name", "Parent Name", "Phone", "Email", "DOB", "Requested Classroom",
    "Final Status", "Date Added", "Archived Date", "Notes",
]


def archive_row(entry_dict):
    """One archive row for the CSV export -- richer than the sheet copy
    (final status, both dates), since this is a standalone compliance
    artifact rather than something meant to round-trip back into the form
    sheet."""
    notes = entry_dict.get("noteLog") or []
    notes_str = " | ".join(f"[{n['ts']}] {n['text']}" for n in notes)
    status = entry_dict.get("status")
    return [
        entry_dict.get("childName", ""),
        entry_dict.get("parentName", ""),
        entry_dict.get("phone", ""),
        entry_dict.get("email", ""),
        entry_dict.get("dob", ""),
        entry_dict.get("requestedClassroom", ""),
        STATUS_LABELS.get(status, status or ""),
        entry_dict.get("timestamp", ""),
        entry_dict.get("archivedAt", ""),
        notes_str,
    ]


# Column layout matching the waitlist Google Form's actual responses sheet --
# 13 columns, including its duplicate "Email Address" column -- so a family's
# row can move cleanly between the source sheet and the archive sheet in
# either direction (archive, or restore back to active).
FORM_HEADERS = [
    "Timestamp", "Parent's name", "Phone Number", "Email Address", "Child's Name",
    "Gender", "Child's Birthday", "Preferred Enrolment Date", "Do you require subsidy?",
    "Do you have any questions for us?", "Email Address",
    "What classroom would you like to be waitlisted for?", "Notes",
]


def form_row(entry_dict):
    """One row in the form-sheet column layout (13 columns, including the
    duplicate Email Address column), built from a WaitlistEntry.to_dict().
    Used for both the archive-sheet copy and for restoring a family back
    into the source sheet."""
    notes = entry_dict.get("noteLog") or []
    notes_str = " | ".join(f"[{n['ts']}] {n['text']}" for n in notes)
    email = entry_dict.get("email", "")
    return [
        entry_dict.get("timestamp", ""),
        entry_dict.get("parentName", ""),
        entry_dict.get("phone", ""),
        email,
        entry_dict.get("childName", ""),
        entry_dict.get("gender", ""),
        entry_dict.get("dob", ""),
        entry_dict.get("preferredDate", ""),
        entry_dict.get("subsidy", ""),
        entry_dict.get("questions", ""),
        email,
        entry_dict.get("requestedClassroom", ""),
        notes_str,
    ]


# ---------------------------------------------------------------------------
# Google Sheets URL parsing
# ---------------------------------------------------------------------------

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_GID_RE = re.compile(r"[#&?]gid=(\d+)")


def parse_sheet_url(url):
    """Pull (sheet_id, gid) out of a pasted Google Sheets URL, e.g.
    https://docs.google.com/spreadsheets/d/<id>/edit?gid=<gid>#gid=<gid>
    gid is None if the URL doesn't specify a tab. Raises ValueError if the
    URL isn't a recognizable Google Sheets link."""
    url = (url or "").strip()
    id_match = _SHEET_ID_RE.search(url)
    if not id_match:
        raise ValueError("That doesn't look like a Google Sheets link (expected a docs.google.com/spreadsheets/d/... URL).")
    gid_match = _GID_RE.search(url)
    return id_match.group(1), (gid_match.group(1) if gid_match else None)
