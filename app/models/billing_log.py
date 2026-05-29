from app.extensions import db
from datetime import datetime, timezone

class BillingIncreaseLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    subscription_id = db.Column(db.String(255), nullable=False)
    stripe_price_id_old = db.Column(db.String(255))
    stripe_price_id_new = db.Column(db.String(255))

    old_amount = db.Column(db.Integer)
    new_amount = db.Column(db.Integer)

    status = db.Column(db.String(50))  # success / skipped / failed
    reason = db.Column(db.String(255), nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc)
    )