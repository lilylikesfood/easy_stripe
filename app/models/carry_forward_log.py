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

    # source invoice snapshot
    source_invoice_number = db.Column(db.String(100), nullable=True)
    source_invoice_created = db.Column(db.DateTime, nullable=True)
    source_invoice_due_date = db.Column(db.DateTime, nullable=True)
    source_invoice_total_cents = db.Column(db.Integer, nullable=True)
    # original invoice total before tax
    source_invoice_total_excluding_tax_cents = db.Column(db.Integer, nullable=True)
    # remaining unpaid amount including tax
    source_invoice_amount_remaining_cents = db.Column(db.Integer, nullable=True)

    # status proof
    old_invoice_status_before = db.Column(db.String(50), nullable=True)
    old_invoice_status_after = db.Column(db.String(50), nullable=True)

    # clearer carry-forward amount
    carried_forward_amount_cents = db.Column(db.Integer, nullable=True)

    # destination / new invoice tracking
    new_invoice_id = db.Column(db.String(100), nullable=True)
    new_invoice_number = db.Column(db.String(100), nullable=True)

    # description shown in Stripe
    carry_forward_description = db.Column(db.String(255), nullable=True)

    livemode = db.Column(db.Boolean, nullable=True)