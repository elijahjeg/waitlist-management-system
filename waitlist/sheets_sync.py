"""
Pulls new waitlist form responses straight from the Google Sheet the Form
writes to, so staff don't have to export/upload a CSV by hand. Runs once at
app startup (see __init__.create_app) and is also exposed as a manual
"Sync now" action via POST /api/entries/sync.

Never raises on missing/misconfigured credentials or network errors -- it
reports the problem in the returned dict so a stale/offline sync doesn't
block the app from starting or loading.
"""
import os
import re
import json
import logging
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import db, WaitlistEntry, Settings
from .paths import bundled_resource_path
from .utils import (
    build_field_map,
    WAITLIST_FIELD_PATTERNS,
    dedupe_key,
    new_note,
    parse_sheet_url,
    form_row,
    FORM_HEADERS,
    STATUS_COLORS_HEX,
    hex_to_rgb_float,
)

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # read/write -- archiving deletes a row from the source sheet

# Defaults point at Impact Childcare's waitlist form responses sheet. Override
# with env vars if the form/sheet ever moves.
DEFAULT_SHEET_ID = "1xy4B0YE9GwqlDvb8Y5jRi_3B7u0pSFS8Vgdx1cknhFs"
DEFAULT_GID = "1809003094"
DEFAULT_KEY_FILE = bundled_resource_path("fast-audio-303101-6ae072de98ed.json")


def _config():
    """Resolve which sheet/tab to sync from. Precedence: a link pasted into
    Settings (easiest for non-technical staff to change) > env var override
    (for advanced/deployment use) > hardcoded default."""
    sheet_id, gid = None, None
    settings = Settings.query.first()
    if settings and settings.sheet_url:
        try:
            sheet_id, gid = parse_sheet_url(settings.sheet_url)
        except ValueError as exc:
            logger.warning("Ignoring stored Sheet URL, couldn't parse it: %s", exc)

    return {
        "sheet_id": sheet_id or os.environ.get("GOOGLE_SHEET_ID", DEFAULT_SHEET_ID),
        "gid": gid or os.environ.get("GOOGLE_SHEET_GID", DEFAULT_GID),
        "key_file": os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", DEFAULT_KEY_FILE),
    }


def _archive_config():
    """Resolve the archive-copy sheet from Settings. None if not configured
    or the stored link can't be parsed."""
    settings = Settings.query.first()
    if not settings or not settings.archive_sheet_url:
        return None
    try:
        sheet_id, gid = parse_sheet_url(settings.archive_sheet_url)
    except ValueError as exc:
        logger.warning("Ignoring stored archive Sheet URL, couldn't parse it: %s", exc)
        return None
    return {"sheet_id": sheet_id, "gid": gid}


def _build_service(key_file):
    if not os.path.exists(key_file):
        return None
    creds = Credentials.from_service_account_file(key_file, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _resolve_tab(service, sheet_id, gid):
    """Return (title, numeric_sheet_id) for the tab matching `gid`, falling
    back to the spreadsheet's first tab if `gid` is None or not found (e.g.
    the tab was renamed). (None, None) if the spreadsheet has no tabs."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = meta.get("sheets", [])
    for sheet in sheets:
        props = sheet.get("properties", {})
        if gid is not None and str(props.get("sheetId")) == str(gid):
            return props.get("title"), props.get("sheetId")
    if not sheets:
        return None, None
    props = sheets[0]["properties"]
    return props.get("title"), props.get("sheetId")


def _fetch_rows(service, sheet_id, gid):
    title, _ = _resolve_tab(service, sheet_id, gid)
    if not title:
        return [], []
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{title}'!A:Z"
    ).execute()
    values = result.get("values", [])
    if not values:
        return [], []
    return values[0], values[1:]


def _imported_records(headers, rows):
    """Sheet rows -> field-mapped dicts, keeping only rows with a child name."""
    fmap = build_field_map(headers, WAITLIST_FIELD_PATTERNS)
    imported = []
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        raw = dict(zip(headers, padded))
        rec = {key: (raw.get(h) or "").strip() for h, key in fmap.items()}
        if rec.get("childName"):
            imported.append(rec)
    return imported


def _new_entry_from_rec(rec, **extra):
    notes_json = "[]"
    if rec.get("notes"):
        notes_json = json.dumps([new_note(rec["notes"])])
    return WaitlistEntry(
        timestamp=rec.get("timestamp") or "",
        parent_name=rec.get("parentName", ""),
        phone=rec.get("phone", ""),
        child_name=rec.get("childName", ""),
        gender=rec.get("gender", ""),
        dob=rec.get("dob", ""),
        preferred_date=rec.get("preferredDate", ""),
        subsidy=rec.get("subsidy", ""),
        questions=rec.get("questions", ""),
        email=rec.get("email", ""),
        requested_classroom=rec.get("requestedClassroom", ""),
        status="none",
        notes_json=notes_json,
        **extra,
    )


def sync_from_sheet():
    """Pull any new waitlist form responses from the Google Sheet, and remove
    local entries whose row has disappeared from it. Safe to call repeatedly.

    Deletion only ever applies to entries confirmed to have come from a sheet
    row (source == "sheet") and still active (not archived) -- manually-added
    families and the archive are never touched here, and rows from before
    this feature existed (source is NULL) are left alone until a sync
    confirms their provenance one way or the other, to avoid guessing wrong
    and silently destroying real notes/status history.

    Must be called inside an app context. Returns a result dict."""
    cfg = _config()

    if not os.path.exists(cfg["key_file"]):
        msg = f"Service account key not found at {cfg['key_file']}"
        logger.warning("Google Sheets sync skipped: %s", msg)
        return {"ok": False, "error": msg}

    try:
        service = _build_service(cfg["key_file"])
        headers, rows = _fetch_rows(service, cfg["sheet_id"], cfg["gid"])
    except HttpError as exc:
        logger.warning("Google Sheets sync: API error: %s", exc)
        return {"ok": False, "error": f"Google Sheets API error: {exc}"}
    except Exception as exc:
        logger.warning("Google Sheets sync: %s", exc)
        return {"ok": False, "error": str(exc)}

    imported = _imported_records(headers, rows)
    sheet_keys = {dedupe_key(rec) for rec in imported}

    all_entries = WaitlistEntry.query.all()
    existing_keys = {dedupe_key(e.to_dict()) for e in all_entries}

    added = 0
    for rec in imported:
        key = dedupe_key(rec)
        if key in existing_keys:
            continue
        db.session.add(_new_entry_from_rec(rec, source="sheet"))
        existing_keys.add(key)
        added += 1

    # Reconcile provenance: an untagged (pre-existing) active entry that
    # currently matches a sheet row is confirmed sheet-sourced from here on.
    for e in all_entries:
        if not e.archived and e.source != "sheet" and dedupe_key(e.to_dict()) in sheet_keys:
            e.source = "sheet"

    # Prune: confirmed sheet-sourced, still-active entries whose row is no
    # longer in the sheet (someone deleted or edited it directly).
    removed_names = []
    for e in all_entries:
        if not e.archived and e.source == "sheet" and dedupe_key(e.to_dict()) not in sheet_keys:
            removed_names.append(e.child_name)
            db.session.delete(e)

    # Retry pushing any manually-added families that haven't made it into the
    # sheet yet (e.g. added while offline, or the initial push failed) --
    # keeps multiple devices/staff converging on the same shared sheet
    # instead of silently diverging. Once pushed, an entry is indistinguishable
    # from one submitted through the Form, including the prune rule above.
    pushed = 0
    for e in all_entries:
        if not e.archived and e.source == "manual":
            try:
                _append_row_and_color(service, cfg["sheet_id"], cfg["gid"], e.to_dict())
                e.source = "sheet"
                pushed += 1
            except (HttpError, ValueError) as exc:
                logger.warning("Sync: couldn't push manual entry '%s' to source sheet: %s", e.child_name, exc)

    settings = Settings.query.first()
    if settings:
        settings.last_synced_at = datetime.now().isoformat()
    db.session.commit()

    if removed_names:
        logger.info("Google Sheets sync: removed %d local entries no longer in the sheet: %s",
                    len(removed_names), ", ".join(removed_names))
    if pushed:
        logger.info("Google Sheets sync: pushed %d manually-added famil%s into the sheet",
                    pushed, "y" if pushed == 1 else "ies")

    return {
        "ok": True,
        "added": added,
        "skipped": len(imported) - added,
        "total": len(imported),
        "removed": len(removed_names),
        "removedNames": removed_names,
        "pushed": pushed,
    }


# ---------------------------------------------------------------------------
# Archiving -- remove the row from the source sheet, copy it to the archive
# sheet (if configured)
# ---------------------------------------------------------------------------

def _find_row(service, sheet_id, gid, entry_dict):
    """Locate the row matching `entry_dict`'s child name + timestamp. Returns
    None if no exact match, otherwise a dict with the row's 1-indexed row
    number (matching the Sheets UI), the tab's numeric sheet id, and its
    column count (for row-wide operations like coloring)."""
    title, numeric_gid = _resolve_tab(service, sheet_id, gid)
    if not title:
        return None
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{title}'!A:Z"
    ).execute()
    values = result.get("values", [])
    if not values:
        return None
    headers, rows = values[0], values[1:]
    fmap = build_field_map(headers, WAITLIST_FIELD_PATTERNS)
    target_key = dedupe_key(entry_dict)

    for i, row in enumerate(rows):
        padded = row + [""] * (len(headers) - len(row))
        raw = dict(zip(headers, padded))
        rec = {key: (raw.get(h) or "").strip() for h, key in fmap.items()}
        if dedupe_key(rec) == target_key:
            return {
                "row_number": i + 2,  # +1 for header row, +1 to go from 0- to 1-indexed
                "numeric_gid": numeric_gid,
                "num_cols": len(headers),
            }
    return None


def _delete_row(service, sheet_id, numeric_gid, row_number):
    body = {
        "requests": [{
            "deleteDimension": {
                "range": {
                    "sheetId": numeric_gid,
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }]
    }
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()


def _color_row(service, sheet_id, numeric_gid, row_number, num_cols, hex_color):
    body = {
        "requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": numeric_gid,
                    "startRowIndex": row_number - 1,
                    "endRowIndex": row_number,
                    "startColumnIndex": 0,
                    "endColumnIndex": max(num_cols, 1),
                },
                "cell": {"userEnteredFormat": {"backgroundColor": hex_to_rgb_float(hex_color)}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }]
    }
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()


def _last_row_from_range(a1_range):
    """'Sheet1'!A5:J6 -> 6 (the last row actually written). Works whether the
    range spans one row or several."""
    if "!" not in a1_range:
        return None
    cell_part = a1_range.split("!", 1)[1]
    nums = re.findall(r"\d+", cell_part)
    return int(nums[-1]) if nums else None


def _append_row_and_color(service, sheet_id, gid, entry_dict):
    """Append `entry_dict` (form-shaped) to the given sheet/tab, writing a
    header row first if it's empty, then color the new data row to match the
    entry's current status. Shared by both directions of the archive <->
    source move."""
    title, numeric_gid = _resolve_tab(service, sheet_id, gid)
    if not title:
        raise ValueError("Sheet has no tabs")

    existing = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{title}'!A1:A1"
    ).execute()
    rows_to_write = []
    if not existing.get("values"):
        rows_to_write.append(FORM_HEADERS)
    rows_to_write.append(form_row(entry_dict))

    append_result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{title}'!A:Z",
        # RAW, not USER_ENTERED -- USER_ENTERED lets Sheets auto-detect and
        # silently convert date/number-looking text (timestamps, DOBs) into
        # serial numbers, corrupting the original string and breaking future
        # dedupe_key matching against it.
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_write},
    ).execute()

    # Color the newly-written data row (not the header) to match its status.
    updated_range = append_result.get("updates", {}).get("updatedRange", "")
    data_row_number = _last_row_from_range(updated_range)
    if data_row_number is not None:
        hex_color = STATUS_COLORS_HEX.get(entry_dict.get("status"), STATUS_COLORS_HEX["none"])
        _color_row(service, sheet_id, numeric_gid, data_row_number, len(FORM_HEADERS), hex_color)


def archive_and_sync(entry_dict):
    """Called after a family is archived locally. Best-effort: removes their
    row from the source sheet, and copies it to the archive sheet if one is
    configured. Never raises -- the local DB is already the source of truth
    for app state, so problems here are reported as warnings rather than
    failing the archive action itself."""
    cfg = _config()
    warnings = []
    archived_in_sheet = False

    if not os.path.exists(cfg["key_file"]):
        return {"ok": False, "warnings": ["Service account key not found; couldn't update Google Sheets."], "archived_in_sheet": False}

    try:
        service = _build_service(cfg["key_file"])
    except Exception as exc:
        logger.warning("Archive sync: couldn't build API client: %s", exc)
        return {"ok": False, "warnings": [f"Couldn't connect to Google Sheets: {exc}"], "archived_in_sheet": False}

    try:
        found = _find_row(service, cfg["sheet_id"], cfg["gid"], entry_dict)
        if found is None:
            warnings.append("Couldn't find this family's row in the source sheet to remove -- it may have already been removed or edited.")
        else:
            _delete_row(service, cfg["sheet_id"], found["numeric_gid"], found["row_number"])
    except HttpError as exc:
        logger.warning("Archive sync: couldn't remove row from source sheet: %s", exc)
        warnings.append(f"Couldn't remove the row from the source sheet: {exc}")

    archive_cfg = _archive_config()
    if archive_cfg:
        try:
            _append_row_and_color(service, archive_cfg["sheet_id"], archive_cfg["gid"], entry_dict)
            archived_in_sheet = True
        except HttpError as exc:
            logger.warning("Archive sync: couldn't append to archive sheet: %s", exc)
            warnings.append(f"Couldn't add the row to the archive sheet: {exc}")
        except ValueError as exc:
            warnings.append(str(exc))

    return {"ok": not warnings, "warnings": warnings, "archived_in_sheet": archived_in_sheet}


def restore_and_sync(entry_dict):
    """Called after a family is restored to active locally. Best-effort:
    removes their row from the archive sheet (if configured) and appends
    them back into the source sheet, colored to match their current status.
    Never raises."""
    cfg = _config()
    warnings = []
    restored_in_sheet = False

    if not os.path.exists(cfg["key_file"]):
        return {"ok": False, "warnings": ["Service account key not found; couldn't update Google Sheets."], "restored_in_sheet": False}

    try:
        service = _build_service(cfg["key_file"])
    except Exception as exc:
        logger.warning("Restore sync: couldn't build API client: %s", exc)
        return {"ok": False, "warnings": [f"Couldn't connect to Google Sheets: {exc}"], "restored_in_sheet": False}

    archive_cfg = _archive_config()
    if archive_cfg:
        try:
            found = _find_row(service, archive_cfg["sheet_id"], archive_cfg["gid"], entry_dict)
            if found is None:
                warnings.append("Couldn't find this family's row in the archive sheet to remove -- it may have already been removed or edited.")
            else:
                _delete_row(service, archive_cfg["sheet_id"], found["numeric_gid"], found["row_number"])
        except HttpError as exc:
            logger.warning("Restore sync: couldn't remove row from archive sheet: %s", exc)
            warnings.append(f"Couldn't remove the row from the archive sheet: {exc}")

    try:
        _append_row_and_color(service, cfg["sheet_id"], cfg["gid"], entry_dict)
        restored_in_sheet = True
    except HttpError as exc:
        logger.warning("Restore sync: couldn't append to source sheet: %s", exc)
        warnings.append(f"Couldn't add the row back to the source sheet: {exc}")
    except ValueError as exc:
        warnings.append(str(exc))

    return {"ok": not warnings, "warnings": warnings, "restored_in_sheet": restored_in_sheet}


def push_new_entry_to_source(entry_dict):
    """Called right after a family is added manually in the app (source ==
    "manual"). Best-effort: appends their row to the source sheet
    immediately, so it's not stuck on just this one device -- other devices
    pick it up on their next sync, and this app's own sync_from_sheet()
    retries automatically if this fails (e.g. offline right now). Never
    raises."""
    cfg = _config()

    if not os.path.exists(cfg["key_file"]):
        return {"ok": False, "warnings": ["Service account key not found; this family is saved locally only for now."], "pushed": False}

    try:
        service = _build_service(cfg["key_file"])
        _append_row_and_color(service, cfg["sheet_id"], cfg["gid"], entry_dict)
        return {"ok": True, "warnings": [], "pushed": True}
    except HttpError as exc:
        logger.warning("Manual entry push: couldn't append to source sheet: %s", exc)
        return {"ok": False, "warnings": [f"Saved locally, but couldn't add it to the shared sheet yet (will retry automatically): {exc}"], "pushed": False}
    except Exception as exc:
        logger.warning("Manual entry push: %s", exc)
        return {"ok": False, "warnings": [str(exc)], "pushed": False}


def color_status_in_source(entry_dict):
    """Called after a family's status tag changes locally. Best-effort: colors
    that family's row in the source sheet to match the tag's color in the app
    (e.g. green for Confirmed). Never raises."""
    cfg = _config()

    if not os.path.exists(cfg["key_file"]):
        return {"ok": False, "warnings": ["Service account key not found; couldn't color the row."]}

    try:
        service = _build_service(cfg["key_file"])
        found = _find_row(service, cfg["sheet_id"], cfg["gid"], entry_dict)
        if found is None:
            return {"ok": False, "warnings": ["Couldn't find this family's row in the source sheet to color."]}
        hex_color = STATUS_COLORS_HEX.get(entry_dict.get("status"), STATUS_COLORS_HEX["none"])
        _color_row(service, cfg["sheet_id"], found["numeric_gid"], found["row_number"], found["num_cols"], hex_color)
        return {"ok": True, "warnings": []}
    except HttpError as exc:
        logger.warning("Status color sync: %s", exc)
        return {"ok": False, "warnings": [f"Couldn't color the row in the source sheet: {exc}"]}
    except Exception as exc:
        logger.warning("Status color sync: %s", exc)
        return {"ok": False, "warnings": [str(exc)]}


# ---------------------------------------------------------------------------
# Archive sheet sync -- mirror of sync_from_sheet(), scoped to archived
# entries and the archive sheet instead of the waitlist form sheet
# ---------------------------------------------------------------------------

def sync_archive_from_sheet():
    """Pulls archived families from the archive sheet that aren't yet known
    locally, and removes local archived records whose row has disappeared
    from it. Same safety rules as sync_from_sheet(): only entries this app
    has confirmed came from the archive sheet (archive_confirmed=True) are
    ever auto-deleted here. No-op if no archive sheet is configured.

    Must be called inside an app context. Returns a result dict."""
    archive_cfg = _archive_config()
    if not archive_cfg:
        return {"ok": False, "error": "No archive sheet configured."}

    cfg = _config()
    if not os.path.exists(cfg["key_file"]):
        msg = f"Service account key not found at {cfg['key_file']}"
        logger.warning("Archive sheet sync skipped: %s", msg)
        return {"ok": False, "error": msg}

    try:
        service = _build_service(cfg["key_file"])
        headers, rows = _fetch_rows(service, archive_cfg["sheet_id"], archive_cfg["gid"])
    except HttpError as exc:
        logger.warning("Archive sheet sync: API error: %s", exc)
        return {"ok": False, "error": f"Google Sheets API error: {exc}"}
    except Exception as exc:
        logger.warning("Archive sheet sync: %s", exc)
        return {"ok": False, "error": str(exc)}

    imported = _imported_records(headers, rows)
    archive_keys = {dedupe_key(rec) for rec in imported}

    all_entries = WaitlistEntry.query.all()
    existing_keys = {dedupe_key(e.to_dict()) for e in all_entries}

    added = 0
    now = datetime.now().isoformat()
    for rec in imported:
        key = dedupe_key(rec)
        if key in existing_keys:
            continue
        db.session.add(_new_entry_from_rec(
            rec, source="sheet", archived=True, archived_at=now, archive_confirmed=True,
        ))
        existing_keys.add(key)
        added += 1

    # Reconcile: an untagged archived entry that currently matches an archive
    # sheet row is confirmed archive-sourced from here on.
    for e in all_entries:
        if e.archived and not e.archive_confirmed and dedupe_key(e.to_dict()) in archive_keys:
            e.archive_confirmed = True

    # Prune: confirmed archive-sourced entries whose row is no longer in the
    # archive sheet (someone deleted or edited it directly there).
    removed_names = []
    for e in all_entries:
        if e.archived and e.archive_confirmed and dedupe_key(e.to_dict()) not in archive_keys:
            removed_names.append(e.child_name)
            db.session.delete(e)

    db.session.commit()

    if removed_names:
        logger.info("Archive sheet sync: removed %d local archived entries no longer in the archive sheet: %s",
                    len(removed_names), ", ".join(removed_names))

    return {
        "ok": True,
        "added": added,
        "skipped": len(imported) - added,
        "total": len(imported),
        "removed": len(removed_names),
        "removedNames": removed_names,
    }
