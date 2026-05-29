from app.extensions import db
from datetime import datetime, timezone

class BillingIncreaseLog(db.Model):
    __tablename__ = "billing_increase_log"

    id = db.Column(db.Integer, primary_key=True)

    # 🔁 batch tracking (VERY IMPORTANT)
    run_id = db.Column(db.String(64), nullable=False, index=True)

    # Execution tracking
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    # 🔗 Stripe references
    subscription_id = db.Column(db.String(255), nullable=False)
    customer_id = db.Column(db.String(255), nullable=True)
    product_id = db.Column(db.String(255), nullable=True)

    stripe_price_id_old = db.Column(db.String(255))
    stripe_price_id_new = db.Column(db.String(255))

    # 💰 pricing
    old_amount = db.Column(db.Integer)
    new_amount = db.Column(db.Integer)
    increase_percentage = db.Column(db.Float)

    # 📌 status tracking
    status = db.Column(db.String(50))  # success / skipped / failed
    reason = db.Column(db.String(255), nullable=True)
    error_code = db.Column(db.String(100), nullable=True)

    # 🧠 debugging snapshots
    old_price_snapshot = db.Column(db.JSON, nullable=True)
    stripe_snapshot = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc)
    )