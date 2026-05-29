from datetime import datetime, timezone

from app.extensions import db


class Contract(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    stripe_customer_id = db.Column(
        db.String,
        nullable=False,
        unique=True
    )

    stripe_subscription_id = db.Column(
        db.String,
        nullable=False,
        unique=True
    )

    contract_start_date = db.Column(
        db.Date,
        nullable=False
    )

    contract_end_date = db.Column(
        db.Date,
        nullable=False
    )

    inspection_end_date = db.Column(
        db.Date,
        nullable=False
    )

    annual_increase_date = db.Column(
        db.Date,
        nullable=False
    )

    inspection_fee_active = db.Column(
        db.Boolean,
        default=True
    )

    contract_active = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
    db.DateTime,
    default=lambda: datetime.now(timezone.utc)
)

updated_at = db.Column(
    db.DateTime,
    default=lambda: datetime.now(timezone.utc),
    onupdate=lambda: datetime.now(timezone.utc)
)