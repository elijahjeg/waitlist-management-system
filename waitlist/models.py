import json
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Bracket(db.Model):
    """An age group used to classify waitlisted children (e.g. Toddler = 18-35 months)."""
    __tablename__ = "brackets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    min_months = db.Column(db.Integer, nullable=False, default=0)
    max_months = db.Column(db.Integer, nullable=False, default=12)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "minMonths": self.min_months, "maxMonths": self.max_months}


class Settings(db.Model):
    """Single-row table for app-wide settings."""
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    lookahead_days = db.Column(db.Integer, nullable=False, default=120)
    last_synced_at = db.Column(db.String(64), nullable=True)
    sheet_url = db.Column(db.Text, nullable=True)  # pasted Google Sheets link for waitlist form responses
    archive_sheet_url = db.Column(db.Text, nullable=True)  # pasted Google Sheets link families are copied to on archive


class WaitlistEntry(db.Model):
    __tablename__ = "waitlist_entries"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.String(64), default="")  # original form-submission timestamp (raw string)
    parent_name = db.Column(db.String(200), default="")
    phone = db.Column(db.String(50), default="")
    child_name = db.Column(db.String(200), default="")
    gender = db.Column(db.String(50), default="")
    dob = db.Column(db.String(50), default="")
    preferred_date = db.Column(db.String(50), default="")
    subsidy = db.Column(db.String(50), default="")
    questions = db.Column(db.Text, default="")
    email = db.Column(db.String(200), default="")
    requested_classroom = db.Column(db.String(120), default="")
    status = db.Column(db.String(20), default="none")
    notes_json = db.Column(db.Text, default="[]")  # JSON list of {id, ts, text}, most recent first
    archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.String(64), nullable=True)
    # "sheet" once confirmed to have come from (and still match a row in) the
    # waitlist form sheet; "manual" for families added via the app directly.
    # Null on rows created before this column existed -- treated as "unknown
    # provenance, don't auto-delete" until a sync confirms one way or the other.
    source = db.Column(db.String(20), nullable=True)
    # True once this (archived) entry's row is confirmed present in the
    # archive sheet -- gates the same auto-delete safety net as `source`,
    # but for the archive sheet instead of the waitlist form sheet.
    archive_confirmed = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "parentName": self.parent_name,
            "phone": self.phone,
            "childName": self.child_name,
            "gender": self.gender,
            "dob": self.dob,
            "preferredDate": self.preferred_date,
            "subsidy": self.subsidy,
            "questions": self.questions,
            "email": self.email,
            "requestedClassroom": self.requested_classroom,
            "status": self.status,
            "noteLog": json.loads(self.notes_json or "[]"),
            "archived": bool(self.archived),
            "archivedAt": self.archived_at,
            "source": self.source,
            "archiveConfirmed": bool(self.archive_confirmed),
        }


class RosterEntry(db.Model):
    """
    A row from the Lillio enrollment export. Wholesale-replaced on each import.

    Note: this deliberately has no "grad date" field. Lillio's export includes a
    Grad date column, but it isn't something staff keep up to date -- when a spot
    actually opens is computed from date of birth against the age-group brackets
    instead (see routes.enrich_roster).
    """
    __tablename__ = "roster_entries"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(120), default="")
    last_name = db.Column(db.String(120), default="")
    dob = db.Column(db.String(50), default="")
    age = db.Column(db.String(20), default="")
    classroom = db.Column(db.String(120), default="")
    status = db.Column(db.String(50), default="")
    enroll_date = db.Column(db.String(50), default="")
    rotation = db.Column(db.String(50), default="")
    schedule = db.Column(db.String(50), default="")

    def to_dict(self):
        return {
            "id": self.id,
            "firstName": self.first_name,
            "lastName": self.last_name,
            "dob": self.dob,
            "age": self.age,
            "classroom": self.classroom,
            "status": self.status,
            "enrollDate": self.enroll_date,
            "rotation": self.rotation,
            "schedule": self.schedule,
        }
