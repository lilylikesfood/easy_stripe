from datetime import datetime, timezone
from app.extensions import db

class CarryForwardLog(db.Model):
    id= db.Column(db.Integer, primary_key=True)

    run_id= db.Column(db.String(100), nullable=False)

    invoice_id= db.Column(db.String(100), nullable=False)
    customer_id= db.Column(db.String(100), nullable=False)
    invoice_item_id= db.Column(db.String(100), nullable=True)

    amount_cents= db.Column(db.Integer, nullable=False)

    status= db.Column(db.String(50), nullable=False)
    old_invoice_status = db.Column(db.String(50), nullable=True)
    reason= db.Column(db.String(255), nullable=True)
    error= db.Column(db.Text, nullable=True)

    created_at= db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))