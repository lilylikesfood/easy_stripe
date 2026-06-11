from datetime import datetime, timezone
from app.extensions import db

class LateFeeLog(db.Model):
    id= db.Column(db.Integer, primary_key=True)

    run_id= db.Column(db.String(100), nullable=False)

    invoice_id= db.Column(db.String(100), nullable=False)
    customer_id= db.Column(db.String(100), nullable=True)
    invoice_item_id= db.Column(db.String(100), nullable=True)

    late_fee_month= db.Column(db.String(7), nullable=False)
    amount_cents= db.Column(db.Integer, nullable=True)

    status= db.Column(db.String(50), nullable=False)
    reason= db.Column(db.String(255), nullable=True)
    error= db.Column(db.Text, nullable=True)

    created_at= db.Column(db.DateTime, nullable=False)