import csv
import io
import json
from datetime import datetime, date

from flask import Blueprint, request, jsonify, send_file

from .models import db, WaitlistEntry, RosterEntry, Bracket, Settings
from .utils import (
    build_field_map,
    ROSTER_FIELD_PATTERNS,
    age_in_months,
    classify_bracket,
    loose_match,
    parse_date,
    add_months,
    new_note,
    parse_sheet_url,
    archive_row,
    ARCHIVE_HEADERS,
)
from . import sheets_sync

api = Blueprint("api", __name__, url_prefix="/api")

DEFAULT_BRACKETS = [
    {"name": "Infant", "minMonths": 0, "maxMonths": 17},
    {"name": "Toddler", "minMonths": 18, "maxMonths": 35},
    {"name": "Preschool", "minMonths": 36, "maxMonths": 59},
    {"name": "Pre-K", "minMonths": 60, "maxMonths": 71},
]


def seed_defaults():
    if Bracket.query.count() == 0:
        for b in DEFAULT_BRACKETS:
            db.session.add(Bracket(name=b["name"], min_months=b["minMonths"], max_months=b["maxMonths"]))
    if Settings.query.count() == 0:
        db.session.add(Settings(lookahead_days=120))
    db.session.commit()


def get_brackets_list():
    return [b.to_dict() for b in Bracket.query.order_by(Bracket.min_months).all()]


def get_settings_row():
    s = Settings.query.first()
    if not s:
        s = Settings(lookahead_days=120)
        db.session.add(s)
        db.session.commit()
    return s


def enrich_entry(e_dict, brackets):
    dob = parse_date(e_dict.get("dob"))
    months = age_in_months(dob)
    bracket = classify_bracket(months, brackets)
    requested = e_dict.get("requestedClassroom")
    mismatch = bool(bracket and requested and not loose_match(bracket["name"], requested))
    e_dict["ageMonths"] = months
    e_dict["currentBracket"] = bracket
    e_dict["mismatch"] = mismatch
    return e_dict


def enrich_roster(r_dict, brackets):
    """
    Compute each enrolled child's current age group and the date they'll age out
    of it, purely from date of birth -- not from Lillio's Grad date field, which
    isn't kept up to date. `nextBracket` is None when the current bracket is the
    last one, meaning the child ages out of the program entirely rather than
    moving to another classroom.
    """
    dob = parse_date(r_dict.get("dob"))
    months = age_in_months(dob)
    bracket = classify_bracket(months, brackets)
    sorted_brackets = sorted(brackets, key=lambda b: b["minMonths"])

    next_bracket = None
    age_out_date = None
    if bracket and dob:
        age_out_date = add_months(dob, bracket["maxMonths"] + 1)
        idx = next((i for i, b in enumerate(sorted_brackets) if b["id"] == bracket["id"]), None)
        if idx is not None and idx + 1 < len(sorted_brackets):
            next_bracket = sorted_brackets[idx + 1]

    classroom = r_dict.get("classroom")
    classroom_mismatch = bool(bracket and classroom and not loose_match(bracket["name"], classroom))

    r_dict["ageMonths"] = months
    r_dict["currentBracket"] = bracket
    r_dict["nextBracket"] = next_bracket
    r_dict["ageOutDate"] = age_out_date.isoformat() if age_out_date else None
    r_dict["classroomMismatch"] = classroom_mismatch
    return r_dict


# ---------------------------------------------------------------------------
# Bootstrap — everything the frontend needs in one call
# ---------------------------------------------------------------------------

@api.get("/bootstrap")
def bootstrap():
    brackets = get_brackets_list()
    entries = [enrich_entry(e.to_dict(), brackets) for e in WaitlistEntry.query.all()]
    roster = [enrich_roster(r.to_dict(), brackets) for r in RosterEntry.query.all()]
    settings = get_settings_row()
    return jsonify({
        "entries": entries,
        "roster": roster,
        "brackets": brackets,
        "lookaheadDays": settings.lookahead_days,
        "lastSyncedAt": settings.last_synced_at,
        "sheetUrl": settings.sheet_url,
        "archiveSheetUrl": settings.archive_sheet_url,
    })


# ---------------------------------------------------------------------------
# Waitlist entries
# ---------------------------------------------------------------------------

@api.post("/entries")
def create_entry():
    data = request.get_json(force=True) or {}
    notes_json = "[]"
    if data.get("notes"):
        notes_json = json.dumps([new_note(data["notes"])])
    e = WaitlistEntry(
        timestamp=datetime.now().isoformat(),
        parent_name=data.get("parentName", ""),
        phone=data.get("phone", ""),
        child_name=data.get("childName", ""),
        gender=data.get("gender", ""),
        dob=data.get("dob", ""),
        preferred_date=data.get("preferredDate", ""),
        subsidy=data.get("subsidy", ""),
        questions=data.get("questions", ""),
        email=data.get("email", ""),
        requested_classroom=data.get("requestedClassroom", ""),
        status="none",
        notes_json=notes_json,
        source="manual",
    )
    db.session.add(e)
    db.session.commit()
    entry_dict = e.to_dict()
    result = enrich_entry(entry_dict, get_brackets_list())
    # Best-effort: push into the shared source sheet right away so this
    # family isn't stuck on just this device. If it fails (e.g. offline),
    # sync_from_sheet() retries automatically on the next sync.
    sync_result = sheets_sync.push_new_entry_to_source(entry_dict)
    if sync_result.get("warnings"):
        result["syncWarnings"] = sync_result["warnings"]
    if sync_result.get("pushed"):
        e.source = "sheet"
        db.session.commit()
        result["source"] = "sheet"
    return jsonify(result), 201


@api.patch("/entries/<int:entry_id>")
def update_entry(entry_id):
    e = db.get_or_404(WaitlistEntry, entry_id)
    data = request.get_json(force=True) or {}
    field_map = {
        "status": "status",
        "requestedClassroom": "requested_classroom",
        "phone": "phone",
        "email": "email",
        "parentName": "parent_name",
        "preferredDate": "preferred_date",
        "subsidy": "subsidy",
    }
    for k, col in field_map.items():
        if k in data:
            setattr(e, col, data[k])
    db.session.commit()
    entry_dict = e.to_dict()
    result = enrich_entry(entry_dict, get_brackets_list())
    if "status" in data:
        sync_result = sheets_sync.color_status_in_source(entry_dict)
        if sync_result.get("warnings"):
            result["syncWarnings"] = sync_result["warnings"]
    return jsonify(result)


@api.post("/entries/<int:entry_id>/notes")
def add_note(entry_id):
    e = db.get_or_404(WaitlistEntry, entry_id)
    text = ((request.get_json(force=True) or {}).get("text") or "").strip()
    if text:
        notes = json.loads(e.notes_json or "[]")
        notes.insert(0, new_note(text))
        e.notes_json = json.dumps(notes)
        db.session.commit()
    return jsonify(enrich_entry(e.to_dict(), get_brackets_list()))


@api.post("/entries/<int:entry_id>/archive")
def archive_entry(entry_id):
    e = db.get_or_404(WaitlistEntry, entry_id)
    e.archived = True
    e.archived_at = datetime.now().isoformat()
    db.session.commit()
    entry_dict = e.to_dict()
    result = enrich_entry(entry_dict, get_brackets_list())
    # Best-effort: remove the row from the source sheet and copy it to the
    # archive sheet (if configured). The local DB is already the source of
    # truth for app state, so a Sheets hiccup here surfaces as a warning
    # rather than failing the archive action itself.
    sync_result = sheets_sync.archive_and_sync(entry_dict)
    if sync_result.get("warnings"):
        result["syncWarnings"] = sync_result["warnings"]
    if sync_result.get("archived_in_sheet"):
        e.archive_confirmed = True
        db.session.commit()
        result["archiveConfirmed"] = True
    return jsonify(result)


@api.post("/entries/<int:entry_id>/restore")
def restore_entry(entry_id):
    e = db.get_or_404(WaitlistEntry, entry_id)
    e.archived = False
    e.archived_at = None
    db.session.commit()
    entry_dict = e.to_dict()
    result = enrich_entry(entry_dict, get_brackets_list())
    # Best-effort: remove the row from the archive sheet (if configured) and
    # add it back to the source sheet, mirroring archive_and_sync in reverse.
    sync_result = sheets_sync.restore_and_sync(entry_dict)
    if sync_result.get("warnings"):
        result["syncWarnings"] = sync_result["warnings"]
    if sync_result.get("restored_in_sheet"):
        e.source = "sheet"
        e.archive_confirmed = False
        db.session.commit()
        result["source"] = "sheet"
        result["archiveConfirmed"] = False
    return jsonify(result)


@api.delete("/entries/<int:entry_id>")
def delete_entry(entry_id):
    e = db.get_or_404(WaitlistEntry, entry_id)
    db.session.delete(e)
    db.session.commit()
    return jsonify({"ok": True})


@api.post("/entries/sync")
def sync_entries():
    result = sheets_sync.sync_from_sheet()
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Sync failed")}), 502
    settings = get_settings_row()
    result["lastSyncedAt"] = settings.last_synced_at
    return jsonify(result)


@api.post("/archive/sync")
def sync_archive():
    result = sheets_sync.sync_archive_from_sheet()
    if not result.get("ok"):
        return jsonify({"error": result.get("error", "Archive sync failed")}), 502
    return jsonify(result)


# ---------------------------------------------------------------------------
# Roster (Lillio export) — wholesale replace on each import
# ---------------------------------------------------------------------------

@api.get("/roster")
def list_roster():
    brackets = get_brackets_list()
    return jsonify([enrich_roster(r.to_dict(), brackets) for r in RosterEntry.query.all()])


@api.post("/roster/import")
def import_roster():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400
    try:
        text = file.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "Couldn't read that file as text (expected a CSV)."}), 400

    reader = csv.DictReader(io.StringIO(text))
    fmap = build_field_map(reader.fieldnames, ROSTER_FIELD_PATTERNS)

    imported = []
    for row in reader:
        rec = {key: (row.get(raw) or "").strip() for raw, key in fmap.items()}
        if rec.get("firstName") or rec.get("lastName"):
            imported.append(rec)

    RosterEntry.query.delete()
    for rec in imported:
        db.session.add(RosterEntry(
            first_name=rec.get("firstName", ""),
            last_name=rec.get("lastName", ""),
            dob=rec.get("dob", ""),
            age=rec.get("age", ""),
            classroom=rec.get("classroom", ""),
            status=rec.get("status", ""),
            enroll_date=rec.get("enrollDate", ""),
            rotation=rec.get("rotation", ""),
            schedule=rec.get("schedule", ""),
        ))
    db.session.commit()
    return jsonify({"count": len(imported)})


# ---------------------------------------------------------------------------
# Archive export
# ---------------------------------------------------------------------------

@api.get("/archive/export")
def export_archive():
    entries = WaitlistEntry.query.filter_by(archived=True).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(ARCHIVE_HEADERS)
    for e in entries:
        writer.writerow(archive_row(e.to_dict()))
    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    filename = f"waitlist-archive-{date.today().isoformat()}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Settings + age-group brackets
# ---------------------------------------------------------------------------

@api.get("/settings")
def get_settings_route():
    s = get_settings_row()
    return jsonify({
        "lookaheadDays": s.lookahead_days,
        "sheetUrl": s.sheet_url,
        "archiveSheetUrl": s.archive_sheet_url,
    })


@api.put("/settings")
def update_settings_route():
    data = request.get_json(force=True) or {}
    s = get_settings_row()
    if "lookaheadDays" in data:
        s.lookahead_days = data["lookaheadDays"]
    for field, col in (("sheetUrl", "sheet_url"), ("archiveSheetUrl", "archive_sheet_url")):
        if field in data:
            raw = (data[field] or "").strip()
            if raw:
                try:
                    parse_sheet_url(raw)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
            setattr(s, col, raw or None)
    db.session.commit()
    return jsonify({
        "lookaheadDays": s.lookahead_days,
        "sheetUrl": s.sheet_url,
        "archiveSheetUrl": s.archive_sheet_url,
    })


@api.post("/brackets")
def create_bracket():
    data = request.get_json(force=True) or {}
    b = Bracket(
        name=data.get("name", "New group"),
        min_months=data.get("minMonths", 0),
        max_months=data.get("maxMonths", 12),
    )
    db.session.add(b)
    db.session.commit()
    return jsonify(b.to_dict()), 201


@api.patch("/brackets/<int:bracket_id>")
def update_bracket(bracket_id):
    b = db.get_or_404(Bracket, bracket_id)
    data = request.get_json(force=True) or {}
    if "name" in data:
        b.name = data["name"]
    if "minMonths" in data:
        b.min_months = data["minMonths"]
    if "maxMonths" in data:
        b.max_months = data["maxMonths"]
    db.session.commit()
    return jsonify(b.to_dict())


@api.delete("/brackets/<int:bracket_id>")
def delete_bracket(bracket_id):
    b = db.get_or_404(Bracket, bracket_id)
    db.session.delete(b)
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Danger zone
# ---------------------------------------------------------------------------

@api.post("/reset")
def reset_all():
    WaitlistEntry.query.delete()
    RosterEntry.query.delete()
    Bracket.query.delete()
    Settings.query.delete()
    db.session.commit()
    seed_defaults()
    return jsonify({"ok": True})
