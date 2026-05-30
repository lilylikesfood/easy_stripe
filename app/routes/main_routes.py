from flask import Blueprint, render_template, request, session, redirect
from app.services.stripe_service import create_customer

from app.services.automation_service import AutomationService

from datetime import date
from dateutil.relativedelta import relativedelta

import stripe

from flask import current_app

from app.extensions import db

from app.models.contract import Contract

from app.models.billing_log import BillingIncreaseLog

main = Blueprint("main", __name__)


@main.route("/")
def home():
    return {
        "message": "Stripe Billing System Running"
    }

@main.route("/test-stripe")
def test_stripe():

    customer = create_customer(
        name="529finaljen",
        email="529finaljen@example.com"
    )

    return {
        "customer_id": customer.id
    }

@main.route("/run-increase")
def run_increase():

    AutomationService.process_annual_increases()

    return {
        "message": "Annual increase job executed"
    }

@main.route("/create-test-subscription")
def create_test_subscription():

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # 1. Create customer
    customer = stripe.Customer.create(
        name="Test Customer",
        email="test@example.com"
    )

    # 2. ADD TEST PAYMENT METHOD 
    payment_method = stripe.PaymentMethod.create(
        type="card",
        card={
            "token": "tok_visa"
        }
    )

    # 3. Attach payment method to customer
    stripe.PaymentMethod.attach(
        payment_method.id,
        customer=customer.id
    )

    # 4. Set as default payment method
    stripe.Customer.modify(
        customer.id,
        invoice_settings={
            "default_payment_method": payment_method.id
        }
    )

    # 5. Create subscription
    subscription = stripe.Subscription.create(
        customer=customer.id,
        items=[
            {
                "price": "price_1TcIpyCj5xmY98cw4AroybOz"
            },
            {
                "price": "price_1TcIrACj5xmY98cwpbsC0a7J"
            }
        ],
        default_payment_method=payment_method.id
    )

    # 6. Create contract in DB
    today = date.today()

    contract = Contract(
        stripe_customer_id=customer.id,
        stripe_subscription_id=subscription.id,
        contract_start_date=today,
        contract_end_date=today + relativedelta(years=50),
        inspection_end_date=today + relativedelta(years=3),
        annual_increase_date=today
    )

    db.session.add(contract)
    db.session.commit()

    return {
        "customer_id": customer.id,
        "subscription_id": subscription.id,
        "contract_id": contract.id
    }


@main.route("/contracts")
def contracts():

    contracts = Contract.query.all()

    data = []

    for contract in contracts:

        data.append({
            "id": contract.id,
            "annual_increase_date": str(contract.annual_increase_date),
            "subscription_id": contract.stripe_subscription_id
        })

    return data

# billing-dashboard
@main.route("/billing-dashboard")
def billing_dashboard():

    if not session.get("logged_in"):
        return redirect("/login")

    logs = BillingIncreaseLog.query.order_by(
        BillingIncreaseLog.created_at.desc()
    ).limit(200).all()

    stats = {
        "total": len(logs),
        "success": len([l for l in logs if l.status == "success"]),
        "skipped": len([l for l in logs if l.status == "skipped"]),
        "failed": len([l for l in logs if l.status == "failed"]),
    }

    return render_template(
        "billing_dashboard.html",
        logs=logs,
        stats=stats
    )

# admin dashboard
ADMIN_PASSWORD = "secret123"

@main.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        password = request.form.get("password")

        if password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/billing-dashboard")

        return "Wrong password", 403

    return render_template("login.html")