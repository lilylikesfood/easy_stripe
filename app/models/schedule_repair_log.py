from app.extensions import db
from datetime import datetime, timezone

class ScheduleRepairLog(db.Model):
    __tablename__ = "schedule_repair_log"

    id = db.Column(db.Integer, primary_key=True)

    run_id = db.Column(db.String(64), nullable=False, index=True)

    subscription_id = db.Column(db.String(255), nullable=False)
    customer_id = db.Column(db.String(255), nullable=True)
    schedule_id = db.Column(db.String(255), nullable=False)

    status = db.Column(db.String(50), nullable=False)  # success / skipped / failed
    reason = db.Column(db.String(255), nullable=True)

    old_price_id = db.Column(db.String(255), nullable=True)
    new_price_id = db.Column(db.String(255), nullable=True)

    old_amount = db.Column(db.Integer, nullable=True)
    new_amount = db.Column(db.Integer, nullable=True)

    phase_start = db.Column(db.Integer, nullable=True)
    phase_end = db.Column(db.Integer, nullable=True)

    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc)
    )