import os
from flask import Flask, render_template
from sqlalchemy import inspect, text

from .models import db
from .paths import app_data_dir


def _ensure_schema():
    """Add columns introduced after some users' waitlist.db already existed.
    db.create_all() only creates missing tables, not missing columns on
    existing ones."""
    inspector = inspect(db.engine)
    if "settings" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("settings")}
        if "last_synced_at" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN last_synced_at TEXT"))
        if "sheet_url" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN sheet_url TEXT"))
        if "archive_sheet_url" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN archive_sheet_url TEXT"))

    if "waitlist_entries" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("waitlist_entries")}
        if "source" not in cols:
            # Left NULL for pre-existing rows on purpose -- unknown provenance
            # means sync won't auto-delete them until it can confirm they're
            # still a real sheet row (see sheets_sync.sync_from_sheet).
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE waitlist_entries ADD COLUMN source TEXT"))
        if "archive_confirmed" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE waitlist_entries ADD COLUMN archive_confirmed BOOLEAN DEFAULT 0"))


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")

    db_path = os.path.join(app_data_dir(), "waitlist.db")

    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from .routes import api, seed_defaults
    app.register_blueprint(api)

    with app.app_context():
        db.create_all()
        _ensure_schema()
        seed_defaults()

    @app.route("/")
    def index():
        return render_template("index.html")

    return app
