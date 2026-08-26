from datetime import datetime, timezone
from app.extensions import db

class ContractEnd50yrLog(db.Model):
    __tablename__ = "contract_end_50yr_log"

    id = db.Column(db.Integer, primary_key=True)

    run_id = db.Column(db.String(100), nullable=False)

    subscription_id = db.Column(db.String(100), nullable=False)
    customer_id = db.Column(db.String(100), nullable=True)

    status = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False)

    livemode = db.Column(db.Boolean, nullable=True)