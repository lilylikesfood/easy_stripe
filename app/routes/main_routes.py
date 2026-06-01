from flask import Blueprint, render_template, request, session, redirect
from app.services.stripe_service import create_customer

from app.services.automation_service import AutomationService

from datetime import date,datetime, timezone
from dateutil.relativedelta import relativedelta

import stripe

from flask import current_app

from app.extensions import db

from app.models.contract import Contract

from app.models.billing_log import BillingIncreaseLog

import os

import uuid

from app.models.schedule_repair_log import ScheduleRepairLog

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

@main.route("/admin/run-increase")
def run_increase():

    if not session.get("logged_in"):
        return redirect("/login")
    
    run_id= str(uuid.uuid4())

    started_at= datetime.now(timezone.utc)

    AutomationService.process_annual_increases(
        run_id=run_id,
        started_at=started_at
    )

    return {
        "message": "Annual increase job executed",
        "run_id": run_id
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
        "total": BillingIncreaseLog.query.count(),
        "success": BillingIncreaseLog.query.filter_by(status="success").count(),
        "skipped": BillingIncreaseLog.query.filter_by(status="skipped").count(),
        "failed": BillingIncreaseLog.query.filter_by(status="failed").count(),
    }

    return render_template(
        "billing_dashboard.html",
        logs=logs,
        stats=stats
    )

# admin dashboard
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
# print("ADMIN PASSWORD:", os.getenv("ADMIN_PASSWORD"))

@main.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        password = request.form.get("password")

        if password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/billing-dashboard")

        return "Wrong password", 403

    return render_template("login.html")

# logout 
@main.route("/logout")
def logout():
    session.clear()

    return redirect("/login")

# debug-scheduled-subscriptions
@main.route("/debug-scheduled-subscriptions")
def debug_scheduled_subscriptions():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    results = []

    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100
    )

    for subscription in subscriptions.auto_paging_iter():

        schedule = (
            subscription["schedule"]
            if "schedule" in subscription
            else None
        )

        if schedule:
            results.append({
                "subscription_id": subscription["id"],
                "customer_id": subscription["customer"],
                "schedule_id": schedule
            })

    return {
        "count": len(results),
        "subscriptions": results
    }

# debug specific client
@main.route("/debug-schedule/<subscription_id>")
def debug_schedule(subscription_id):

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    subscription = stripe.Subscription.retrieve(subscription_id)

    schedule_id = (
        subscription["schedule"]
        if "schedule" in subscription
        else None
    )

    return {
        "subscription_id": subscription["id"],
        "schedule_id": schedule_id
    }

# 
@main.route("/debug-schedule-details/<schedule_id>")
def debug_schedule_details(schedule_id):

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

    return schedule._to_dict_recursive()

# 
@main.route("/debug-schedule-prices/<schedule_id>")
def debug_schedule_prices(schedule_id):

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

    result = []

    for phase in schedule["phases"]:
        phase_data = {
            "start_date": phase["start_date"],
            "end_date": phase["end_date"],
            "items": []
        }

        for item in phase["items"]:
            price = stripe.Price.retrieve(item["price"])
            product = stripe.Product.retrieve(price["product"])

            phase_data["items"].append({
                "price_id": price["id"],
                "unit_amount": price["unit_amount"],
                "product_id": product["id"],
                "product_name": product["name"],
                "increaseable": product["metadata"]["increaseable"] if "increaseable" in product["metadata"] else None
            })

        result.append(phase_data)

    return {
        "schedule_id": schedule_id,
        "phases": result
    }

# testing for repairing specific clients's scheduled update (to remove scheduled update)
@main.route("/admin/repair-schedule/<subscription_id>")
def repair_schedule(subscription_id):

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    subscription = stripe.Subscription.retrieve(subscription_id)

    schedule_id = subscription["schedule"] if "schedule" in subscription else None

    if not schedule_id:
        return {
            "status": "skipped",
            "reason": "subscription has no schedule"
        }

    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

    # Map product_id -> current increased price_id
    current_prices_by_product = {}

    for item in subscription["items"]["data"]:
        price = item["price"]
        product = stripe.Product.retrieve(price["product"])

        increaseable = (
            product["metadata"]["increaseable"]
            if "increaseable" in product["metadata"]
            else None
        )

        if str(increaseable).lower() == "true":
            current_prices_by_product[price["product"]] = price["id"]

    new_phases = []

    for phase in schedule["phases"]:
        new_items = []

        for item in phase["items"]:
            old_price = stripe.Price.retrieve(item["price"])
            product_id = old_price["product"]

            replacement_price_id = (
                current_prices_by_product[product_id]
                if product_id in current_prices_by_product
                else item["price"]
            )

            new_items.append({
                "price": replacement_price_id,
                "quantity": item["quantity"] if "quantity" in item else 1,
            })

        new_phases.append({
            "items": new_items,
            "start_date": phase["start_date"],
            "end_date": phase["end_date"],
            "proration_behavior": (
                phase["proration_behavior"]
                if "proration_behavior" in phase
                else "none"
            ),
        })

    updated_schedule = stripe.SubscriptionSchedule.modify(
        schedule_id,
        phases=new_phases
    )

    return {
        "status": "success",
        "subscription_id": subscription_id,
        "schedule_id": schedule_id,
        "updated_schedule_id": updated_schedule["id"]
    }

# diagnostic route
@main.route("/admin/diagnose-schedule-rollbacks")
def diagnose_schedule_rollbacks():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    results = []
    checked = 0
    with_schedule = 0
    rollback_risk = 0

    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100
    )

    for subscription in subscriptions.auto_paging_iter():
        checked += 1

        schedule_id = subscription["schedule"] if "schedule" in subscription else None

        if not schedule_id:
            continue

        with_schedule += 1

        schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

        # current increaseable product -> current price amount
        current_amount_by_product = {}

        for sub_item in subscription["items"]["data"]:
            current_price = sub_item["price"]
            product = stripe.Product.retrieve(current_price["product"])

            increaseable = (
                product["metadata"]["increaseable"]
                if "increaseable" in product["metadata"]
                else None
            )

            if str(increaseable).lower() == "true":
                current_amount_by_product[current_price["product"]] = {
                    "price_id": current_price["id"],
                    "unit_amount": current_price["unit_amount"],
                    "product_name": product["name"]
                }

        risks = []

        for phase in schedule["phases"]:
            for item in phase["items"]:
                phase_price = stripe.Price.retrieve(item["price"])
                product_id = phase_price["product"]

                if product_id not in current_amount_by_product:
                    continue

                current_info = current_amount_by_product[product_id]

                if phase_price["unit_amount"] < current_info["unit_amount"]:
                    risks.append({
                        "phase_start": phase["start_date"],
                        "phase_end": phase["end_date"],
                        "product_name": current_info["product_name"],
                        "current_price_id": current_info["price_id"],
                        "current_amount": current_info["unit_amount"],
                        "phase_price_id": phase_price["id"],
                        "phase_amount": phase_price["unit_amount"]
                    })

        if risks:
            rollback_risk += 1
            results.append({
                "subscription_id": subscription["id"],
                "customer_id": subscription["customer"],
                "schedule_id": schedule_id,
                "risks": risks
            })

    return {
        "checked": checked,
        "with_schedule": with_schedule,
        "rollback_risk": rollback_risk,
        "results": results
    }

# group risky subscriptions
@main.route("/admin/rollback-summary")
def rollback_summary():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    summary = {}
    checked = 0
    rollback_risk = 0

    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100
    )

    for subscription in subscriptions.auto_paging_iter():
        checked += 1

        schedule_id = subscription["schedule"] if "schedule" in subscription else None

        if not schedule_id:
            continue

        schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

        current_amount_by_product = {}

        for sub_item in subscription["items"]["data"]:
            current_price = sub_item["price"]
            product = stripe.Product.retrieve(current_price["product"])

            increaseable = (
                product["metadata"]["increaseable"]
                if "increaseable" in product["metadata"]
                else None
            )

            if str(increaseable).lower() == "true":
                current_amount_by_product[current_price["product"]] = {
                    "current_price_id": current_price["id"],
                    "current_amount": current_price["unit_amount"],
                    "product_name": product["name"]
                }

        for phase in schedule["phases"]:
            for item in phase["items"]:
                phase_price = stripe.Price.retrieve(item["price"])
                product_id = phase_price["product"]

                if product_id not in current_amount_by_product:
                    continue

                current_info = current_amount_by_product[product_id]

                if phase_price["unit_amount"] < current_info["current_amount"]:
                    rollback_risk += 1

                    old_price_id = phase_price["id"]

                    if old_price_id not in summary:
                        summary[old_price_id] = {
                            "old_price_id": old_price_id,
                            "old_amount": phase_price["unit_amount"],
                            "current_amount_examples": [],
                            "count": 0,
                            "subscriptions": []
                        }

                    summary[old_price_id]["count"] += 1
                    summary[old_price_id]["subscriptions"].append(subscription["id"])

                    if len(summary[old_price_id]["current_amount_examples"]) < 3:
                        summary[old_price_id]["current_amount_examples"].append({
                            "subscription_id": subscription["id"],
                            "current_price_id": current_info["current_price_id"],
                            "current_amount": current_info["current_amount"],
                            "schedule_id": schedule_id
                        })

    return {
        "checked": checked,
        "rollback_risk_items": rollback_risk,
        "group_count": len(summary),
        "summary": list(summary.values())
    }
# repair all schedule rollbacks
@main.route("/admin/repair-all-schedule-rollbacks")
def repair_all_schedule_rollbacks():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    run_id = str(uuid.uuid4())

    checked = 0
    repaired = 0
    skipped = 0
    failed = 0
    details = []

    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100
    )

    for subscription in subscriptions.auto_paging_iter():
        checked += 1

        try:
            schedule_id = subscription["schedule"] if "schedule" in subscription else None

            if not schedule_id:
                skipped += 1
                continue

            schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

            current_price_by_product = {}

            for sub_item in subscription["items"]["data"]:
                current_price = sub_item["price"]
                product = stripe.Product.retrieve(current_price["product"])

                increaseable = (
                    product["metadata"]["increaseable"]
                    if "increaseable" in product["metadata"]
                    else None
                )

                if str(increaseable).lower() == "true":
                    current_price_by_product[current_price["product"]] = {
                        "price_id": current_price["id"],
                        "amount": current_price["unit_amount"]
                    }

            has_risk = False
            new_phases = []

            for phase in schedule["phases"]:
                new_items = []

                for item in phase["items"]:
                    phase_price = stripe.Price.retrieve(item["price"])
                    product_id = phase_price["product"]

                    replacement_price_id = item["price"]
                    replacement_amount = phase_price["unit_amount"]

                    if product_id in current_price_by_product:
                        current_info = current_price_by_product[product_id]

                        if phase_price["unit_amount"] < current_info["amount"]:
                            has_risk = True
                            replacement_price_id = current_info["price_id"]
                            replacement_amount = current_info["amount"]

                            log = ScheduleRepairLog(
                                run_id=run_id,
                                subscription_id=subscription["id"],
                                customer_id=subscription["customer"],
                                schedule_id=schedule_id,
                                status="success",
                                reason="future schedule phase price replaced to prevent rollback",
                                old_price_id=phase_price["id"],
                                new_price_id=current_info["price_id"],
                                old_amount=phase_price["unit_amount"],
                                new_amount=current_info["amount"],
                                phase_start=phase["start_date"],
                                phase_end=phase["end_date"],
                            )

                            db.session.add(log)

                    new_items.append({
                        "price": replacement_price_id,
                        "quantity": item["quantity"] if "quantity" in item else 1,
                    })

                new_phases.append({
                    "items": new_items,
                    "start_date": phase["start_date"],
                    "end_date": phase["end_date"],
                    "proration_behavior": (
                        phase["proration_behavior"]
                        if "proration_behavior" in phase
                        else "none"
                    ),
                })

            if not has_risk:
                skipped += 1
                continue

            stripe.SubscriptionSchedule.modify(
                schedule_id,
                phases=new_phases
            )

            repaired += 1
            db.session.commit()

            details.append({
                "subscription_id": subscription["id"],
                "schedule_id": schedule_id,
                "status": "repaired"
            })

        except Exception as e:
            db.session.rollback()
            failed += 1

            log = ScheduleRepairLog(
                run_id=run_id,
                subscription_id=subscription["id"],
                customer_id=subscription["customer"] if "customer" in subscription else None,
                schedule_id=schedule_id if "schedule_id" in locals() else None,
                status="failed",
                reason="schedule repair failed",
                error_message=str(e),
            )

            db.session.add(log)
            db.session.commit()

            details.append({
                "subscription_id": subscription["id"],
                "status": "failed",
                "error": str(e)
            })

    return {
        "run_id": run_id,
        "checked": checked,
        "repaired": repaired,
        "skipped": skipped,
        "failed": failed,
        "details": details
    }

# view repai logs
@main.route("/admin/schedule-repair-logs")
def schedule_repair_logs():

    if not session.get("logged_in"):
        return redirect("/login")

    logs = ScheduleRepairLog.query.order_by(
        ScheduleRepairLog.created_at.desc()
    ).limit(200).all()

    return {
        "count": ScheduleRepairLog.query.count(),
        "logs": [
            {
                "id": log.id,
                "run_id": log.run_id,
                "subscription_id": log.subscription_id,
                "customer_id": log.customer_id,
                "schedule_id": log.schedule_id,
                "status": log.status,
                "reason": log.reason,
                "old_price_id": log.old_price_id,
                "new_price_id": log.new_price_id,
                "old_amount": log.old_amount,
                "new_amount": log.new_amount,
                "phase_start": log.phase_start,
                "phase_end": log.phase_end,
                "created_at": str(log.created_at),
                "error_message": log.error_message,
            }
            for log in logs
        ]
    }