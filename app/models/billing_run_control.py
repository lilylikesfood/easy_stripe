from app.extensions import db
from datetime import datetime, timezone

class BillingRunControl(db.Model):
    __tablename__ = "billing_run_control"

    id = db.Column(db.Integer, primary_key=True)

    run_date = db.Column(db.String(20), unique=True, nullable=False)  # e.g. "2026-06-01"
    run_id = db.Column(db.String(64), nullable=False)

    status = db.Column(db.String(20))  # success / failed

    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = db.Column(db.DateTime, nullable=True)