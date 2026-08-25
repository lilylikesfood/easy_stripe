from flask import Blueprint, render_template, request, session, redirect
from app.services.stripe_service import create_customer

from app.services.automation_service import AutomationService

from datetime import date,datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

import stripe

from flask import current_app

from app.extensions import db

from app.models.contract import Contract

from app.models.billing_log import BillingIncreaseLog

import os

import uuid

from app.models.schedule_repair_log import ScheduleRepairLog

from app.services.pricing_service import PricingService

from app.models.late_fee_log import LateFeeLog

from app.models.carry_forward_log import CarryForwardLog

from pprint import pprint

from app.scheduler.scheduler import TORONTO_TZ

import calendar

from flask import Response
import csv
import io

import json

import time

main = Blueprint("main", __name__)

# If that condition is true, we return True immediately — meaning "treat this person as logged in."
# Otherwise, fall back to the login 
def logged_in_or_dev():
    if os.getenv("ALLOW_ADMIN_WITHOUT_LOGIN") == "true":
        return True
    
    return session.get("logged_in", False)

def is_live_mode():
    return current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

@main.route("/")
def home():
    return {
        "message": "Stripe Billing System Running"
    }

@main.route("/test-stripe", methods=["POST"])
def test_stripe():
    if request.form.get("confirm") != "APPLY":
        return {"error": "confirmation required"}, 400

    customer = create_customer(
        name="529finaljen",
        email="529finaljen@example.com"
    )

    return {
        "customer_id": customer.id
    }

@main.route("/admin/run-increase", methods=["POST"])
def run_increase():

    if not session.get("logged_in"):
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route."
        }, 400
    
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

@main.route("/create-test-subscription", methods=["POST"])
def create_test_subscription():
    if request.form.get("confirm") != "APPLY":
        return {"error": "confirmation required"}, 400

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

    billing_increase_logs = BillingIncreaseLog.query.order_by(
        BillingIncreaseLog.created_at.desc()
    ).limit(20).all()

    billing_increase_stats = {
        "total": BillingIncreaseLog.query.count(),
        "success": BillingIncreaseLog.query.filter_by(status="success").count(),
        "skipped": BillingIncreaseLog.query.filter_by(status="skipped").count(),
        "failed": BillingIncreaseLog.query.filter_by(status="failed").count(),
    }

    late_fee_logs= LateFeeLog.query.order_by(
        LateFeeLog.created_at.desc()
    ).limit(20).all()

    late_fee_stats= {
        "total": LateFeeLog.query.count(),
        "success":LateFeeLog.query.filter_by(status="success").count(),
        "skipped":LateFeeLog.query.filter_by(status="skipped").count(),
        "failed":LateFeeLog.query.filter_by(status="failed").count(),
    }

    carry_forward_logs= CarryForwardLog.query.order_by(
        CarryForwardLog.created_at.desc()
    ).limit(20).all()

    carry_forward_stats= {
        "total": CarryForwardLog.query.count(),
        "success":CarryForwardLog.query.filter_by(status="success").count(),
        "skipped":CarryForwardLog.query.filter_by(status="skipped").count(),
        "failed":CarryForwardLog.query.filter_by(status="failed").count(),
    }

    return render_template(
        "billing_dashboard.html",
        billing_increase_logs=billing_increase_logs,
        billing_increase_stats=billing_increase_stats,
        late_fee_logs=late_fee_logs,
        late_fee_stats=late_fee_stats,
        carry_forward_logs=carry_forward_logs,
        carry_forward_stats=carry_forward_stats,
        TORONTO_TZ=TORONTO_TZ,
        timezone=timezone,
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
@main.route("/admin/repair-schedule/<subscription_id>", methods=["POST"])
def repair_schedule(subscription_id):

    if not session.get("logged_in"):
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route."
        }, 400

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

    billable_statuses = [
        "active",
        "past_due",
        "unpaid"
    ]

    results = []
    checked = 0
    with_schedule = 0
    rollback_risk = 0

    for status in billable_statuses:

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():
            checked += 1

            schedule_id = subscription["schedule"] if "schedule" in subscription else None

            if not schedule_id:
                continue

            with_schedule += 1

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
                    "status": subscription["status"],
                    "schedule_id": schedule_id,
                    "risks": risks
                })

    return {
        "checked": checked,
        "billable_statuses_checked": billable_statuses,
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
@main.route("/admin/repair-all-schedule-rollbacks", methods=["POST"])
def repair_all_schedule_rollbacks():

    if not session.get("logged_in"):
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route."
        }, 400

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
                                livemode=is_live_mode(),
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
                livemode= is_live_mode(),
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

# diagnostic route that scans all subscriptions
@main.route("/admin/schedule-health-check")
def schedule_health_check():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    checked = 0
    with_schedule = 0

    issue_counts = {
        "no_schedule": 0,
        "rollback_risk": 0,
        "no_phases": 0,
        "empty_phase_items": 0,
        "future_phase_missing_increaseable_product": 0,
        "wrong_number_of_increaseable_items": 0,
        "schedule_not_active": 0,
    }

    issues = []

    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100
    )

    for subscription in subscriptions.auto_paging_iter():
        checked += 1

        subscription_id = subscription["id"]
        customer_id = subscription["customer"]

        schedule_id = subscription["schedule"] if "schedule" in subscription else None

        if not schedule_id:
            issue_counts["no_schedule"] += 1
            issues.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "issue": "no_schedule",
                "message": "Active subscription has no subscription schedule"
            })
            continue

        with_schedule += 1

        try:
            schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

            if schedule["status"] != "active":
                issue_counts["schedule_not_active"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "schedule_id": schedule_id,
                    "issue": "schedule_not_active",
                    "status": schedule["status"]
                })

            phases = schedule["phases"] if "phases" in schedule else []

            if not phases:
                issue_counts["no_phases"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "schedule_id": schedule_id,
                    "issue": "no_phases",
                    "message": "Schedule has no phases"
                })
                continue

            # Current subscription increaseable products
            current_increaseable_by_product = {}

            for sub_item in subscription["items"]["data"]:
                current_price = sub_item["price"]
                product = stripe.Product.retrieve(current_price["product"])

                increaseable = (
                    product["metadata"]["increaseable"]
                    if "increaseable" in product["metadata"]
                    else None
                )

                if str(increaseable).lower() == "true":
                    current_increaseable_by_product[current_price["product"]] = {
                        "price_id": current_price["id"],
                        "amount": current_price["unit_amount"],
                        "product_name": product["name"]
                    }

            if len(current_increaseable_by_product) != 1:
                issue_counts["wrong_number_of_increaseable_items"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "schedule_id": schedule_id,
                    "issue": "wrong_number_of_increaseable_items",
                    "count": len(current_increaseable_by_product),
                    "message": "Expected exactly one increaseable monthly fee item on current subscription"
                })

            for phase in phases:
                phase_items = phase["items"] if "items" in phase else []

                if not phase_items:
                    issue_counts["empty_phase_items"] += 1
                    issues.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "schedule_id": schedule_id,
                        "issue": "empty_phase_items",
                        "phase_start": phase["start_date"] if "start_date" in phase else None,
                        "phase_end": phase["end_date"] if "end_date" in phase else None
                    })
                    continue

                phase_increaseable_count = 0

                for item in phase_items:
                    phase_price = stripe.Price.retrieve(item["price"])
                    product_id = phase_price["product"]

                    product = stripe.Product.retrieve(product_id)

                    increaseable = (
                        product["metadata"]["increaseable"]
                        if "increaseable" in product["metadata"]
                        else None
                    )

                    if str(increaseable).lower() == "true":
                        phase_increaseable_count += 1

                        if product_id not in current_increaseable_by_product:
                            issue_counts["future_phase_missing_increaseable_product"] += 1
                            issues.append({
                                "subscription_id": subscription_id,
                                "customer_id": customer_id,
                                "schedule_id": schedule_id,
                                "issue": "future_phase_missing_increaseable_product",
                                "phase_price_id": phase_price["id"],
                                "product_id": product_id,
                                "message": "Future phase has increaseable product not found on current subscription"
                            })
                            continue

                        current_info = current_increaseable_by_product[product_id]

                        if phase_price["unit_amount"] < current_info["amount"]:
                            issue_counts["rollback_risk"] += 1
                            issues.append({
                                "subscription_id": subscription_id,
                                "customer_id": customer_id,
                                "schedule_id": schedule_id,
                                "issue": "rollback_risk",
                                "current_price_id": current_info["price_id"],
                                "current_amount": current_info["amount"],
                                "phase_price_id": phase_price["id"],
                                "phase_amount": phase_price["unit_amount"],
                                "phase_start": phase["start_date"] if "start_date" in phase else None,
                                "phase_end": phase["end_date"] if "end_date" in phase else None
                            })

                if phase_increaseable_count != len(current_increaseable_by_product):
                    issue_counts["future_phase_missing_increaseable_product"] += 1
                    issues.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "schedule_id": schedule_id,
                        "issue": "future_phase_missing_increaseable_product",
                        "phase_start": phase["start_date"] if "start_date" in phase else None,
                        "phase_end": phase["end_date"] if "end_date" in phase else None,
                        "expected_count": len(current_increaseable_by_product),
                        "actual_count": phase_increaseable_count,
                        "message": "Future phase does not have the same number of increaseable items as current subscription"
                    })

        except Exception as e:
            issues.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "schedule_id": schedule_id,
                "issue": "exception",
                "error": str(e)
            })

    return {
        "checked": checked,
        "with_schedule": with_schedule,
        "issue_counts": issue_counts,
        "issues_found": len(issues),
        "issues": issues
    }

# Who missed an increase, Who has extra inspection fees, Who has missing inspection fees, Who has schedule problems, Who has rollback risks
@main.route("/admin/audit-increase-coverage")
def audit_increase_coverage():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    current_year = "2026"

    billable_statuses = [
        "active",
        "past_due",
        "unpaid"
    ]

    checked = 0
    issues = []

    issue_counts = {
        "missing_2026_increase": 0,
        "wrong_increaseable_count": 0,
        "no_subscription_items": 0,
        "price_retrieve_failed": 0,
        "product_retrieve_failed": 0,
    }

    for status in billable_statuses:

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():
            checked += 1

            subscription_id = subscription["id"]
            customer_id = subscription["customer"]
            subscription_status = subscription["status"]

            last_increase_year = (
                subscription["metadata"]["last_increase_year"]
                if "metadata" in subscription
                and "last_increase_year" in subscription["metadata"]
                else None
            )

            if last_increase_year != current_year:
                issue_counts["missing_2026_increase"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "status": subscription_status,
                    "issue": "missing_2026_increase",
                    "last_increase_year": last_increase_year
                })

            items = subscription["items"]["data"] if "items" in subscription else []

            if not items:
                issue_counts["no_subscription_items"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "status": subscription_status,
                    "issue": "no_subscription_items"
                })
                continue

            increaseable_items = []

            for item in items:
                try:
                    price = stripe.Price.retrieve(item["price"]["id"])
                except Exception as e:
                    issue_counts["price_retrieve_failed"] += 1
                    issues.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "status": subscription_status,
                        "issue": "price_retrieve_failed",
                        "error": str(e)
                    })
                    continue

                try:
                    product = stripe.Product.retrieve(price["product"])
                except Exception as e:
                    issue_counts["product_retrieve_failed"] += 1
                    issues.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "status": subscription_status,
                        "issue": "product_retrieve_failed",
                        "price_id": price["id"],
                        "error": str(e)
                    })
                    continue

                increaseable = (
                    product["metadata"]["increaseable"]
                    if "metadata" in product
                    and "increaseable" in product["metadata"]
                    else None
                )

                if str(increaseable).lower() == "true":
                    increaseable_items.append({
                        "price_id": price["id"],
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "unit_amount": price["unit_amount"],
                    })

            if len(increaseable_items) != 1:
                issue_counts["wrong_increaseable_count"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "status": subscription_status,
                    "issue": "wrong_increaseable_count",
                    "count": len(increaseable_items),
                    "increaseable_items": increaseable_items,
                    "message": "Expected exactly one increaseable monthly fee item"
                })

    return {
        "checked": checked,
        "billable_statuses_checked": billable_statuses,
        "issues_found": len(issues),
        "issue_counts": issue_counts,
        "issues": issues
    }

# manually increase specific customer to increase 3%
@main.route("/admin/run-increase-one/<subscription_id>", methods=["POST"])
def run_increase_one(subscription_id):

    if not session.get("logged_in"):
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route."
        }, 400

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    result = PricingService.apply_annual_increase(
        subscription_id=subscription_id,
        run_id=run_id,
        started_at=started_at
    )

    return {
        "run_id": run_id,
        "subscription_id": subscription_id,
        "result": result
    }

# audit different status counts
@main.route("/admin/audit-subscription-population")
def audit_subscription_population():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    statuses = [
        "active",
        "past_due",
        "unpaid",
        "trialing",
        "canceled",
        "incomplete",
        "incomplete_expired",
        "paused",
    ]

    counts = {}
    examples = {}

    for status in statuses:
        counts[status] = 0
        examples[status] = []

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():
            counts[status] += 1

            if len(examples[status]) < 5:
                examples[status].append({
                    "subscription_id": subscription["id"],
                    "customer_id": subscription["customer"],
                    "status": subscription["status"],
                })

    billable_count = (
        counts["active"]
        + counts["past_due"]
        + counts["unpaid"]
    )

    return {
        "counts": counts,
        "billable_statuses": ["active", "past_due", "unpaid"],
        "billable_count": billable_count,
        "examples": examples
    }

# missing increase and run increase (for status thats not active)
@main.route("/admin/run-increase-missing-billable", methods=["POST"])
def run_increase_missing_billable():

    if not session.get("logged_in"):
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route."
        }, 400

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    current_year = "2026"
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    billable_statuses = [
        "active",
        "past_due",
        "unpaid"
    ]

    checked = 0
    attempted = 0
    skipped = 0
    failed = 0
    results = []

    for status in billable_statuses:

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():
            checked += 1

            last_increase_year = (
                subscription["metadata"]["last_increase_year"]
                if "metadata" in subscription
                and "last_increase_year" in subscription["metadata"]
                else None
            )

            if last_increase_year == current_year:
                skipped += 1
                continue

            try:
                attempted += 1

                result = PricingService.apply_annual_increase(
                    subscription_id=subscription["id"],
                    run_id=run_id,
                    started_at=started_at
                )

                results.append({
                    "subscription_id": subscription["id"],
                    "customer_id": subscription["customer"],
                    "status": subscription["status"],
                    "result": result
                })

            except Exception as e:
                failed += 1

                log = BillingIncreaseLog(
                    run_id=run_id,
                    subscription_id=subscription["id"],
                    customer_id=subscription["customer"],
                    status="failed",
                    reason=str(e),
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    livemode=is_live_mode(),
                )

                db.session.add(log)
                db.session.commit()

                results.append({
                    "subscription_id": subscription["id"],
                    "customer_id": subscription["customer"],
                    "status": subscription["status"],
                    "result": "failed",
                    "error": str(e)
                })

    return {
        "run_id": run_id,
        "checked": checked,
        "attempted": attempted,
        "skipped": skipped,
        "failed": failed,
        "results": results
    }

# list_no_schedule_subscriptions
@main.route("/admin/list-no-schedule-subscriptions")
def list_no_schedule_subscriptions():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    billable_statuses = [
        "active",
        "past_due",
        "unpaid"
    ]

    results = []

    for status in billable_statuses:

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():

            schedule_id = (
                subscription["schedule"]
                if "schedule" in subscription
                else None
            )

            if schedule_id:
                continue

            last_increase_year = (
                subscription["metadata"]["last_increase_year"]
                if "metadata" in subscription
                and "last_increase_year" in subscription["metadata"]
                else None
            )

            results.append({
                "subscription_id": subscription["id"],
                "customer_id": subscription["customer"],
                "status": subscription["status"],
                "last_increase_year": last_increase_year
            })

    return {
        "count": len(results),
        "subscriptions": results
    }

# audit contract ending
@main.route("/admin/audit-contract-ending")
def audit_contract_ending():

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    billable_statuses = [
        "active",
        "past_due",
        "unpaid"
    ]

    checked = 0

    issue_counts = {
        "has_schedule_end": 0,
        "has_cancel_at": 0,
        "has_cancel_at_period_end": 0,
        "no_end_mechanism": 0,
        "schedule_retrieve_failed": 0,
    }

    issues = []
    results = []

    for status in billable_statuses:

        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():
            checked += 1

            subscription_id = subscription["id"]
            customer_id = subscription["customer"]
            subscription_status = subscription["status"]

            schedule_id = (
                subscription["schedule"]
                if "schedule" in subscription
                else None
            )

            cancel_at = (
                subscription["cancel_at"]
                if "cancel_at" in subscription
                else None
            )

            cancel_at_period_end = (
                subscription["cancel_at_period_end"]
                if "cancel_at_period_end" in subscription
                else False
            )

            end_mechanisms = []

            if cancel_at:
                issue_counts["has_cancel_at"] += 1
                end_mechanisms.append("cancel_at")

            if cancel_at_period_end:
                issue_counts["has_cancel_at_period_end"] += 1
                end_mechanisms.append("cancel_at_period_end")

            schedule_end_behavior = None
            schedule_end_date = None

            if schedule_id:
                try:
                    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

                    schedule_end_behavior = (
                        schedule["end_behavior"]
                        if "end_behavior" in schedule
                        else None
                    )

                    phases = (
                        schedule["phases"]
                        if "phases" in schedule
                        else []
                    )

                    if phases:
                        last_phase = phases[-1]
                        schedule_end_date = (
                            last_phase["end_date"]
                            if "end_date" in last_phase
                            else None
                        )

                    if schedule_end_behavior == "cancel" and schedule_end_date:
                        issue_counts["has_schedule_end"] += 1
                        end_mechanisms.append("schedule_end")

                except Exception as e:
                    issue_counts["schedule_retrieve_failed"] += 1
                    issues.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "status": subscription_status,
                        "schedule_id": schedule_id,
                        "issue": "schedule_retrieve_failed",
                        "error": str(e)
                    })

            if not end_mechanisms:
                issue_counts["no_end_mechanism"] += 1
                issues.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "status": subscription_status,
                    "schedule_id": schedule_id,
                    "cancel_at": cancel_at,
                    "cancel_at_period_end": cancel_at_period_end,
                    "issue": "no_end_mechanism",
                    "message": "No schedule end, cancel_at, or cancel_at_period_end found"
                })

            results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "status": subscription_status,
                "schedule_id": schedule_id,
                "schedule_end_behavior": schedule_end_behavior,
                "schedule_end_date": schedule_end_date,
                "cancel_at": cancel_at,
                "cancel_at_period_end": cancel_at_period_end,
                "end_mechanisms": end_mechanisms
            })

    return {
        "checked": checked,
        "billable_statuses_checked": billable_statuses,
        "issue_counts": issue_counts,
        "issues_found": len(issues),
        "issues": issues,
        "results": results
    }

# How much money is sitting in open invoices right now, grouped by customer and age
@main.route("/admin/audit-outstanding-balances")
def audit_outstanding_balances():
    import stripe
    from datetime import datetime, timezone

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    now = datetime.now(timezone.utc)

    def get_overdue_bucket(days_overdue):
        if days_overdue <= 30:
            return "0-30"
        elif days_overdue <= 60:
            return "31-60"
        elif days_overdue <= 90:
            return "61-90"
        else:
            return "90+"

    totals = {
        "total_open_invoice_amount": 0,
        "invoice_count": 0,
        "customers_count": 0,
        "aging": {
            "not_due_yet": 0,
            "0-30": 0,
            "31-60": 0,
            "61-90": 0,
            "90+": 0,
        },
    }

    customers = {}

    invoices = stripe.Invoice.list(
        status="open",
        limit=100,
        expand=["data.customer", "data.subscription"]
    )

    for invoice in invoices.auto_paging_iter():
        amount_remaining = stripe_get(invoice, "amount_remaining", 0)

        if amount_remaining <= 0:
            continue

        customer = stripe_get(invoice, "customer")
        subscription = stripe_get(invoice, "subscription")

        if isinstance(customer, str):
            customer_id = customer
            customer_email = None
            customer_name = None
            customer_balance = 0
        else:
            customer_id = stripe_get(customer, "id")
            customer_email = stripe_get(customer, "email")
            customer_name = stripe_get(customer, "name")
            customer_balance = stripe_get(customer, "balance", 0)

        if isinstance(subscription, str):
            subscription_id = subscription
            subscription_status = None
        else:
            subscription_id = stripe_get(subscription, "id")
            subscription_status = stripe_get(subscription, "status")

        due_date_ts = stripe_get(invoice, "due_date")
        finalized_at_ts = stripe_get(invoice, "finalized_at")
        created_ts = stripe_get(invoice, "created")

        if due_date_ts:
            effective_due_date = datetime.fromtimestamp(due_date_ts, tz=timezone.utc)
        else:
            created_date = datetime.fromtimestamp(created_ts, tz=timezone.utc)
            effective_due_date = created_date + timedelta(days=20)

        raw_age_days = (now.date() - effective_due_date.date()).days

        if raw_age_days < 0:
            days_overdue = 0
            days_until_due = abs(raw_age_days)
            bucket = "not_due_yet"
        else:
            days_overdue = raw_age_days
            days_until_due = 0
            bucket = get_overdue_bucket(days_overdue)

        # debug for siddharth_jan@hotmail.com
        if customer_email == "siddharth_jan@hotmail.com":
            print("\n===== SIDDHARTH =====")

            print("Invoice ID:", stripe_get(invoice, "id"))

            print("Due Date TS:", due_date_ts)

            print("Created TS:", created_ts)

            print("Effective Due Date:", effective_due_date.date())

            print("Days Overdue:", days_overdue)

            print("Bucket:", bucket)

            print("Amount:", cents_to_money(amount_remaining))

            print("=====================\n")

            print(
                "Created:",
                datetime.fromtimestamp(
                    created_ts,
                    tz=timezone.utc
                ).date()
            )

            if due_date_ts:
                print(
                    "Stripe Due Date:",
                    datetime.fromtimestamp(
                        due_date_ts,
                        tz=timezone.utc
                    ).date()
                )


        totals["total_open_invoice_amount"] += amount_remaining
        totals["invoice_count"] += 1
        totals["aging"][bucket] += amount_remaining

        if customer_id not in customers:
            customers[customer_id] = {
                "customer_id": customer_id,
                "name": customer_name,
                "email": customer_email,
                "stripe_customer_balance": cents_to_money(customer_balance),
                "total_amount_remaining": 0,
                "invoice_count": 0,
                "subscription_statuses": set(),
                "aging": {
                    "not_due_yet": 0,
                    "0-30": 0,
                    "31-60": 0,
                    "61-90": 0,
                    "90+": 0,
                },
                "invoices": [],
            }

        customers[customer_id]["total_amount_remaining"] += amount_remaining
        customers[customer_id]["invoice_count"] += 1
        customers[customer_id]["aging"][bucket] += amount_remaining

        if subscription_status:
            customers[customer_id]["subscription_statuses"].add(subscription_status)

        customers[customer_id]["invoices"].append({
            "invoice_id": stripe_get(invoice, "id"),
            "subscription_id": subscription_id,
            "subscription_status": subscription_status,
            "amount_remaining": cents_to_money(amount_remaining),
            "aging_bucket": bucket,
            "days_overdue": days_overdue,
            "days_until_due": days_until_due,
            "collection_method": stripe_get(invoice, "collection_method"),
            "due_date": due_date_ts,
            "effective_due_date": effective_due_date.date().isoformat(),
            "finalized_at": finalized_at_ts,
            "created": created_ts,
            "hosted_invoice_url": stripe_get(invoice, "hosted_invoice_url"),
        })

    customer_rows = []

    for customer in customers.values():
        customer["total_amount_remaining"] = cents_to_money(
            customer["total_amount_remaining"]
        )

        for bucket in customer["aging"]:
            customer["aging"][bucket] = cents_to_money(customer["aging"][bucket])

        customer["subscription_statuses"] = list(customer["subscription_statuses"])
        customer_rows.append(customer)

    customer_rows.sort(
        key=lambda c: c["total_amount_remaining"],
        reverse=True
    )

    totals["customers_count"] = len(customer_rows)
    totals["total_open_invoice_amount"] = cents_to_money(
        totals["total_open_invoice_amount"]
    )

    for bucket in totals["aging"]:
        totals["aging"][bucket] = cents_to_money(totals["aging"][bucket])

    return {
        "summary": totals,
        "customers": customer_rows,
    }

@main.route("/admin/test-group-open-invoices")
def test_group_open_invoices(): 
    customers= {}

    open_invoice= stripe.Invoice.list(
        status="open",
        limit=100
    )

    # grouped invoices by customer
    for invoice in open_invoice.auto_paging_iter():
        amount_remaining = invoice["amount_remaining"]

        if amount_remaining <= 0:
            continue

        customer_id= invoice["customer"]

        if customer_id not in customers:
            customers[customer_id] = 0

        customers[customer_id] += 1

    # categorization
    one_invoice_customers = 0
    two_invoice_customers= 0
    three_plus_invoice_customers = 0 

    for customer_id, invoice_count in customers.items():
        if invoice_count == 1:
            one_invoice_customers +=1
        elif invoice_count == 2:
            two_invoice_customers +=1
        else:
            three_plus_invoice_customers +=1

    return {
        "summary": {
            "one_invoice_customers": one_invoice_customers,
            "two_invoice_customers": two_invoice_customers,
            "three_plus_invoice_customers": three_plus_invoice_customers,
            "total_customers": (
                one_invoice_customers
                + two_invoice_customers
                + three_plus_invoice_customers
            )
        }
    }

# audit-two-open-invoice-customers to see who
@main.route("/admin/audit-multiple-open-invoice-customers")
def audit_multiple_open_invoice_customers():
    def cents_to_money(cents):
        return round((cents or 0)/ 100, 2)

    # login check
    if not session.get("logged_in"):
        return redirect("/login")
    # stripe api key
    stripe.api_key= current_app.config["STRIPE_SECRET_KEY"]

    customers = {}

    now= datetime.now(timezone.utc)

    open_invoices = stripe.Invoice.list(
        status="open",
        limit=100,
        expand=["data.customer"]
    )

    # STEP 1
    # Loop through all open invoices
    for invoice in open_invoices.auto_paging_iter():
        # get customer id
        customer= invoice["customer"]

        if isinstance(customer, str):
            customer_id= customer
            customer_name= None
            customer_email= None
        else: 
            customer_id= customer["id"]
            customer_name= customer["name"] if "name" in customer else None
            customer_email= customer["email"] if "email" in customer else None

        # get invoice id
        invoice_id= invoice["id"]
        # get amount remaining
        amount_remaining= invoice["amount_remaining"]
        # if customer doesn't exist
            # create customer structure
        if customer_id not in customers:
            customers[customer_id] = {
                "customer_id": customer_id,
                "name": customer_name,
                "email": customer_email,
                "invoice_count": 0,
                "total_amount_remaining": 0,
                "invoices": []
            }
        # increment invoice count
        customers[customer_id]["invoice_count"] += 1
        # add amount remaining
        customers[customer_id]["total_amount_remaining"] += amount_remaining

        # convert timestamp to human-readable date
        # stripe gives human-unreadable timestamp
        due_date_ts= invoice["due_date"]

        # debugging not guessing to see whos none
        if due_date_ts is None:
            print(
                "NO DUE DATE:",
                invoice["id"],
                invoice["collection_method"]
            )

        # not every invoice has a due date
        # send_invoice → has due_date
        # charge_automatically → often due_date is None
        if due_date_ts:
            due_date_datetime= datetime.fromtimestamp(due_date_ts, tz=timezone.utc)

            raw_days= (now - due_date_datetime).days

            #positive raw_days  = overdue
            # negative raw_days  = not due yet 
            if raw_days > 0:
                days_overdue = raw_days
                days_until_due = 0
            else: 
                days_overdue = 0
                # abs() : absolute value -> remove the negative sign
                days_until_due = abs(raw_days)

            due_date= due_date_datetime.date().isoformat()

        else:
            due_date = None
            days_overdue = None
            days_until_due = None

        # save invoice id
        customers[customer_id]["invoices"].append({
            "invoice_id": invoice_id,
            "amount_remaining": cents_to_money(amount_remaining),
            "collection_method": invoice["collection_method"],
            # "due_date_ts": invoice["due_date"],
            "due_date": due_date,
            "days_overdue": days_overdue,
            "days_until_due": days_until_due,
            "hosted_invoice_url": invoice["hosted_invoice_url"]
        })

    # STEP 2
    # Build results list

    results = []

    customers_with_1_overdue_1_not_due = 0
    customers_with_2_overdue =0

    # loop through customers
    for customer_id, customer_data in customers.items():
        # keep only customers with 2+ invoices
        if customer_data["invoice_count"] >= 2:
        # append to results
            results.append(customer_data)

            # tell normal timing or true delinquency
            overdue_invoice_count= 0
            
            for invoice in customer_data["invoices"]: 
                if invoice["days_overdue"] is not None and invoice["days_overdue"] > 0: 
                    overdue_invoice_count += 1

            if overdue_invoice_count == 1:
                customers_with_1_overdue_1_not_due +=1
            elif overdue_invoice_count >=2:
                customers_with_2_overdue +=1

    # STEP 3
    # sort highest balance first
    results.sort(
        # def get_balance(customer):
            # return customer["total_amount_remaining"]
        key=lambda customer: customer["total_amount_remaining"],
        # reverse=True: largest → smallest
        reverse=True
        )

    # convert cents to dollars for display
    for customer in results:
        customer["total_amount_remaining"]= cents_to_money(
            customer["total_amount_remaining"]
        )
    # STEP 4
    # return summary + results
    return {
        "summary": {
            "customers_with_multiple_open_invoices": len(results),
            "customers_with_1_overdue_1_not_due": customers_with_1_overdue_1_not_due,
            "customers_with_2_overdue": customers_with_2_overdue
        }, 
        "customers": results
    }

# helper functions
def stripe_get(obj, key, default=None):
    if obj is None:
        return default
    if key in obj:
        return obj[key]
    return default

def cents_to_money(cents):
    return round((cents or 0) / 100, 2)

# check invoice item metadata
@main.route("/admin/debug-invoice-item/<invoice_item_id>")
def debug_invoice_item(invoice_item_id):
    stripe.api_key= current_app.config["STRIPE_SECRET_KEY"]

    invoice_item= stripe.InvoiceItem.retrieve(invoice_item_id)

    pricing = stripe_get(invoice_item, "pricing", {}) or {}

    price_details = stripe_get(pricing, "price_details", {}) or {}

    price_id = stripe_get(price_details, "price")

    product_id = stripe_get(price_details, "product")

    price = (
        stripe.Price.retrieve(price_id) if price_id else None
    )

    product = (
        stripe.Product.retrieve(product_id) if product_id  else None
    )

    return {
        "invoice_item": invoice_item._to_dict_recursive(),
        "price_id": price_id,
        "product_id": product_id,
        "price_tax_behavior": (
            stripe_get(price, "tax_behavior") if price else None
        ),
        "product_tax_code": (
            stripe_get(product, "tax_code") if product else None
        ),
    }

# get latest invoice item for invoice
LATE_FEE_INTERVAL_DAYS = 30

def get_latest_late_fee_for_invoice(customer_id, source_invoice_id):
    invoice_items= stripe.InvoiceItem.list(
        customer= customer_id,
        limit=100
    )

    latest_item = None

    for invoice_item in invoice_items.auto_paging_iter():
        metadata = stripe_get(invoice_item, "metadata", {})

        if (
            stripe_get(metadata, "type") == "late_fee"
            and stripe_get(metadata, "source_invoice_id") == source_invoice_id
        ):
            invoice_item_date = stripe_get(invoice_item, "date", 0)
            latest_item_date = stripe_get(latest_item, "date", 0)

            if latest_item is None or invoice_item_date > latest_item_date:
                latest_item = invoice_item

    return latest_item

def has_recent_late_fee(customer_id, source_invoice_id):
    latest_late_fee = get_latest_late_fee_for_invoice(customer_id, source_invoice_id)

    if not latest_late_fee:
        return False
    
    now_toronto = datetime.now(TORONTO_TZ)

    latest_fee_ts = stripe_get(latest_late_fee, "date")

    if not latest_fee_ts:
        return False

    latest_fee_date = datetime.fromtimestamp(latest_fee_ts, tz=timezone.utc).astimezone(TORONTO_TZ)

    days_since_last_fee = (now_toronto.date() - latest_fee_date.date()).days

    return days_since_last_fee < LATE_FEE_INTERVAL_DAYS
    # return true or false

# def late_fee_already_exists(customer_id, source_invoice_id, late_fee_month):
#     invoice_items= stripe.InvoiceItem.list(
#         customer=customer_id,
#         limit=100
#     )

#     for invoice_item in invoice_items.auto_paging_iter():
#         metadata= stripe_get(invoice_item, "metadata", {})

#         if (
#             stripe_get(metadata, "type") == "late_fee"
#             and stripe_get(metadata, "source_invoice_id") == source_invoice_id
#             and stripe_get(metadata, "late_fee_month") == late_fee_month
#         ):
#             return True
        
#     return False

def stripe_timestamp_to_utc_datetime(ts):
    if not ts: 
        return None
    
    return datetime.fromtimestamp(ts, tz=timezone.utc)

def create_carry_forward_log_from_result(run_id, result):
    log = CarryForwardLog(
        run_id=run_id,

        invoice_id=result.get("invoice_id"),
        customer_id=result.get("customer_id"),
        invoice_item_id=result.get("invoice_item_id"),

        amount_cents=(
            result.get("carried_forward_amount_cents")
            or result.get("intended_carry_forward_amount_cents")
            or result.get("amount_remaining_cents")
            or 0
        ),        
        carried_forward_amount_cents=result.get("carried_forward_amount_cents"),

        status=result.get("status"),

        # Legacy field kept for backward compatibility
        old_invoice_status=result.get("old_invoice_status_after"),
        old_invoice_status_before=result.get("old_invoice_status_before"),
        old_invoice_status_after=result.get("old_invoice_status_after"),

        source_invoice_number=result.get("source_invoice_number"),
        source_invoice_created=stripe_timestamp_to_utc_datetime(result.get("source_invoice_created_ts")),
        source_invoice_due_date=stripe_timestamp_to_utc_datetime(result.get("source_invoice_due_date_ts")),
        source_invoice_total_cents=result.get("source_invoice_total_cents"),
        source_invoice_total_excluding_tax_cents=result.get("source_total_excluding_tax_cents"),
        source_invoice_amount_remaining_cents=result.get("source_invoice_amount_remaining_cents"),

        new_invoice_id=result.get("new_invoice_id"),
        new_invoice_number=result.get("new_invoice_number"),

        carry_forward_description=result.get("carry_forward_description"),

        reason=result.get("reason"),
        error=result.get("error"),
        livemode=is_live_mode(),
    )

    return log

# find people with open invoice
def find_late_fee_candidates():
    stripe.api_key= current_app.config["STRIPE_SECRET_KEY"]

    now_utc = datetime.now(timezone.utc)
    now_toronto = datetime.now(TORONTO_TZ)

    late_fee_month= now_toronto.strftime("%Y-%m")
    # June 2026 → "2026-06"
    # tesing
    # late_fee_month = "2026-07"
    
    def money_to_cents(amount):
        return int(round(amount * 100))
    
    def calculate_late_fee_cents(amount_remaining_cents):
        return int(round(amount_remaining_cents * 0.015))
    
    late_fee_candidates = []
    total_late_fee_cents = 0

    invoices= stripe.Invoice.list(
        status="open", 
        limit=100,
        expand=["data.customer", "data.subscription"]
    )

    for invoice in invoices.auto_paging_iter():
        amount_remaining= stripe_get(invoice, "amount_remaining", 0)
        currency= stripe_get(invoice, "currency")

        if currency != "cad":
            continue

        if amount_remaining <= 0:
            continue

        customer= stripe_get(invoice, "customer")

        if isinstance(customer, str):
            customer_id = customer
            customer_name = None
            customer_email = None
        else:
            customer_id = stripe_get(customer, "id")
            customer_name = stripe_get(customer, "name")
            customer_email = stripe_get(customer, "email")

        subscription, subscription_lookup_source = resolve_invoice_subscription(invoice,customer_id)

        if subscription:
            subscription_id = stripe_get(subscription, "id")
            current_period_end_ts = stripe_get(subscription, "current_period_end")
            billing_cycle_anchor_ts = stripe_get(subscription, "billing_cycle_anchor")

            if current_period_end_ts:
                next_invoice_date = datetime.fromtimestamp(
                    current_period_end_ts,
                    tz=timezone.utc
                ).astimezone(TORONTO_TZ).date().isoformat()

                next_invoice_date_source = "current_period_end"

            elif billing_cycle_anchor_ts:
                next_invoice_date = get_next_monthly_billing_date_from_anchor(
                    billing_cycle_anchor_ts,
                    now_toronto.date()
                ).isoformat()

                next_invoice_date_source = "billing_cycle_anchor_fallback"

            else:
                next_invoice_date = None
                next_invoice_date_source = "missing"

        else:
            subscription_id = None
            next_invoice_date = None
            next_invoice_date_source = "no_subscription"

        due_date_ts= stripe_get(invoice, "due_date")
        created_ts= stripe_get(invoice, "created")

        if due_date_ts:
            effective_due_date = datetime.fromtimestamp(due_date_ts, tz=timezone.utc).astimezone(TORONTO_TZ)

        else:
            created_date = datetime.fromtimestamp(created_ts, tz=timezone.utc).astimezone(TORONTO_TZ)

            effective_due_date = created_date + timedelta(days=20)

        raw_days = (now_toronto.date() - effective_due_date.date()).days
        # for testing
        # raw_days = 10

        if raw_days <= 0:
            continue

        days_overdue= raw_days

        invoice_id= stripe_get(invoice, "id")
        invoice_number = stripe_get(invoice, "number")

        try: 
            late_fee_cents, base_cents = calculate_compounding_late_fee_cents(invoice)
        except Exception as e:
            late_fee_candidates.append({
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_email": customer_email,
                "invoice_id": invoice_id,
                "invoice_number": invoice_number,
                "eligible_to_apply": False,
                "skip_reason": f"late fee calculation failed: {e}",
                "late_fee_rate": "1.5%",
                "late_fee_base": 0,
                "late_fee_base_cents": 0,
                "late_fee": 0,
                "late_fee_cents": 0,
                "invoice_url": stripe_get(invoice, "hosted_invoice_url"),
                "days_overdue": days_overdue,
                "effective_due_date": effective_due_date.date().isoformat(),
                "collection_method": stripe_get(invoice, "collection_method"),
                "amount_remaining": cents_to_money(amount_remaining),
                "reason": "calculation_error",
                "subscription_id": subscription_id,
            }) 

            continue

        already_applied= has_recent_late_fee(
            customer_id,
            invoice_id,
        )

        can_apply = (
            not already_applied
            and subscription_id is not None
        )

        if can_apply:
            total_late_fee_cents += late_fee_cents

        late_fee_candidates.append({
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "collection_method": stripe_get(invoice, "collection_method"),
            "amount_remaining": cents_to_money(amount_remaining),
            "effective_due_date": effective_due_date.date().isoformat(),
            "days_overdue": days_overdue,
            "late_fee_month": late_fee_month,
            "eligible_to_apply": can_apply,
            "skip_reason": (
                "late fee already applied within last 30 days" if already_applied 
                else (
                    f"Could not safely resolve a billable subscription: "
                    f"{subscription_lookup_source}"
                    if subscription_id is None else None
                )
            ),
            "late_fee_rate": "1.5%",
            "late_fee_base": cents_to_money(base_cents),
            "late_fee_base_cents": base_cents,
            "late_fee": cents_to_money(late_fee_cents),
            "late_fee_cents": late_fee_cents,
            "total_after_late_fee": cents_to_money(
                base_cents + late_fee_cents
            ),
            "invoice_url": stripe_get(invoice, "hosted_invoice_url"),
            "reason": "overdue under contract logic",
            "subscription_id": subscription_id,
            "next_invoice_date": next_invoice_date,
            "next_invoice_date_source": next_invoice_date_source,
            "subscription_lookup_source": subscription_lookup_source,
        })

    late_fee_candidates.sort(
        key=lambda candidate: candidate["days_overdue"],
        reverse=True
    )

    return {
        "summary": {
            "late_fee_month": late_fee_month,
            "candidate_count": len(late_fee_candidates),
            "eligible_count": sum(1 for c in late_fee_candidates if c["eligible_to_apply"]),
            "skipped_count": sum(1 for c in late_fee_candidates if not c["eligible_to_apply"]),
            "total_late_fee": f"${cents_to_money(total_late_fee_cents):.2f}"
        },
        "candidates": late_fee_candidates
    }

# audit-current-period-fields
@main.route("/admin/audit-current-period-fields")
def audit_current_period_fields():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    statuses = ["active", "past_due", "unpaid"]

    summary = {
        "checked": 0,
        "has_current_period_end": 0,
        "missing_current_period_end": 0,
    }

    examples = []

    for status in statuses:
        subscription =stripe.Subscription.list(
            status=status,
            limit=100
        )

        for sub in subscription.auto_paging_iter():
            summary["checked"] += 1

            current_period_end = stripe_get(sub, "current_period_end")

            if current_period_end:
                summary["has_current_period_end"] += 1

            else:
                summary["missing_current_period_end"] += 1

                # gives you enough data to inspect the pattern without flooding the response
                if len(examples) < 20:
                    examples.append({
                        "subscription_id": stripe_get(sub, "id"),
                        "customer_id": stripe_get(sub, "customer"),
                        "status": stripe_get(sub, "status"),
                        "collection_method": stripe_get(sub, "collection_method"),
                        "current_period_start": stripe_get(sub, "current_period_start"),
                        "current_period_end": stripe_get(sub, "current_period_end"),
                        "billing_cycle_anchor": stripe_get(sub, "billing_cycle_anchor"),
                        "schedule": stripe_get(sub, "schedule"),
                    })

    return {
        "summary": summary,
        "examples_missing_current_period_end": examples,
    }

# find where Stripe stores the exact upcoming invoice date
@main.route("/admin/debug-upcoming-invoice/<subscription_id>")
def debug_upcoming_invoice(subscription_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    try:
        upcoming = stripe.Invoice.upcoming(subscription=subscription_id)

        return {
            "subscription_id": subscription_id,
            "upcoming_invoice_id": stripe_get(upcoming, "id"),
            "upcoming_created": stripe_get(upcoming, "created"),
            "upcoming_next_payment_attempt": stripe_get(upcoming, "next_payment_attempt"),
            "upcoming_period_start": stripe_get(upcoming, "period_start"),
            "upcoming_period_end": stripe_get(upcoming, "period_end"),
            "upcoming_due_date": stripe_get(upcoming, "due_date"),
            "amount_due": stripe_get(upcoming, "amount_due"),
            "status": "success"
        }
    
    except Exception as e:
        return {
            "subscription_id": subscription_id,
            "status": "failed",
            "error": str(e)
        }
    
# next place to inspect is the subscription schedule current/next phase
@main.route("/admin/debug-schedule-next-date/<subscription_id>")
def debug_schedule_next_date(subscription_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    subscription = stripe.Subscription.retrieve(subscription_id)
    schedule_id = stripe_get(subscription, "schedule")

    if not schedule_id:
        return {
            "subscription_id": subscription_id,
            "status": "no_schedule"
        }

    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

    phases = []

    for phase in stripe_get(schedule, "phases", []):
        phases.append({
            "start_date": stripe_timestamp_to_utc_datetime(
                stripe_get(phase, "start_date")
            ).date().isoformat() if stripe_get(phase, "start_date") else None,
            "end_date": stripe_timestamp_to_utc_datetime(
                stripe_get(phase, "end_date")
            ).date().isoformat() if stripe_get(phase, "end_date") else None,
            "billing_cycle_anchor": stripe_get(phase, "billing_cycle_anchor"),
        })

    return {
        "subscription_id": subscription_id,
        "schedule_id": schedule_id,
        "schedule_status": stripe_get(schedule, "status"),
        "end_behavior": stripe_get(schedule, "end_behavior"),
        "subscription_billing_cycle_anchor": stripe_get(subscription, "billing_cycle_anchor"),
        "phases": phases
    }

# If we ran today,who would receive a late fee and how much?
@main.route("/admin/audit-late-fees")
def audit_late_fees():
    if not logged_in_or_dev():
        return redirect("/login")
    
    return find_late_fee_candidates()
    # def get_name():
    #     return "Lily"
    # You could do:
    # name = get_name()
    # return name
    # or simply:
    # return get_name()
    # Same result.

# get_previous_late_fee_total_cents
def get_previous_late_fee_total_cents(customer_id, source_invoice_id):
    invoice_items= stripe.InvoiceItem.list(
        customer=customer_id,
    )

    total_cents = 0

    for invoice_item in invoice_items.auto_paging_iter():
        metadata= stripe_get(invoice_item, "metadata", {})

        # if (
        #     stripe_get(metadata, "type") == "late_fee" 
        #     and stripe_get(metadata, "source_invoice_id") == source_invoice_id):
        #     total_cents += stripe_get(invoice_item, "amount", 0)

        if (
            stripe_get(metadata, "type") == "late_fee"
            and stripe_get(metadata, "source_invoice_id") == source_invoice_id
        ):
            total_cents += stripe_get(invoice_item, "amount", 0)

    return total_cents

# Compounding late fee calculation:
# New late fee = 1.5% of
# (pre-tax overdue invoice amount + all previous late fees
# for the same source invoice)
def calculate_compounding_late_fee_cents(invoice):
    customer= stripe_get(invoice, "customer")
    source_invoice_id= stripe_get(invoice, "id")
    pretax_overdue_amount_cents = stripe_get(invoice, "total_excluding_tax")

    if isinstance(customer, str):
        customer_id = customer
    else:
        customer_id = stripe_get(customer, "id")

    if customer_id is None:
        raise Exception("Invoice is missing customer_id")
    if source_invoice_id is None:
        raise Exception("Invoice is missing source_invoice_id")
    if pretax_overdue_amount_cents is None:
        raise Exception("Invoice is missing total_excluding_tax")

    if pretax_overdue_amount_cents <= 0:
        raise Exception(
            "Invoice has no positive pre-tax overdue amount"
        )
    
    previous_late_fee_total_cents= get_previous_late_fee_total_cents(customer_id, source_invoice_id)

    base_cents= pretax_overdue_amount_cents + previous_late_fee_total_cents

    late_fee_cents = int(round(base_cents * 0.015))

    print(f"Late fee calculated for invoice {source_invoice_id}: pretax={pretax_overdue_amount_cents}, previous_fees={previous_late_fee_total_cents}, new_fee={late_fee_cents}")

    return late_fee_cents, base_cents

def format_invoice_period(start_ts, end_ts):
    if not start_ts or not end_ts:
        return "invoice period unavailable"
    
    start= datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end= datetime.fromtimestamp(end_ts, tz=timezone.utc)

    if start.year == end.year:
        start_text= f"{start.strftime('%b')} {start.day}"
        end_text= f"{end.strftime('%b')} {end.day}, {end.year}"
    else: 
        start_text= f"{start.strftime('%b')} {start.day}, {start.year}"
        end_text= f"{end.strftime('%b')} {end.day}, {end.year}"

    return f"{start_text} - {end_text}"

# Helper function
# business logic
def apply_late_fee_to_invoice(invoice_id):   
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    now_utc = datetime.now(timezone.utc)
    now_toronto = datetime.now(TORONTO_TZ)

    late_fee_month = now_toronto.strftime("%Y-%m")
    # testing
    # late_fee_month = "2026-07"

    # def calculate_late_fee_cents(amount_remaining_cents):
    #     return int(round(amount_remaining_cents * 0.015))

    # retrieve invoice
    invoice = stripe.Invoice.retrieve(invoice_id)
    invoice_number= stripe_get(invoice, "number") or invoice_id
    invoice_status= stripe_get(invoice, "status")
    amount_remaining= stripe_get(invoice, "amount_remaining", 0)
    customer_id= stripe_get(invoice, "customer")
    due_date_ts= stripe_get(invoice, "due_date")
    created_ts= stripe_get(invoice, "created")

    period_start_ts=stripe_get(invoice, "period_start")
    period_end_ts= stripe_get(invoice, "period_end")

    invoice_period= format_invoice_period(period_start_ts, period_end_ts)

    # validate invoice is open
    if invoice_status != "open":
        return {
            "status": "skipped",
            "reason": "Invoice is not open. ",
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
            "invoice_status": invoice_status
        }

    # Currency validation
    invoice_currency = stripe_get(invoice, "currency")
    if invoice_currency != "cad":
        return {
            "status": "skipped",
            "reason": f"Invoice currency is {invoice_currency}, not CAD.",
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
        }

    # validate amount remaining
    if amount_remaining <= 0:
        return {
            "status": "skipped",
            "reason": "Invoice has no remaining balance", 
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
        }

    # calculate effective due date using contract logic
    if due_date_ts:
        effective_due_date = datetime.fromtimestamp(due_date_ts, tz=timezone.utc).astimezone(TORONTO_TZ)
    else:
        effective_due_date = datetime.fromtimestamp(created_ts, tz=timezone.utc).astimezone(TORONTO_TZ) + timedelta(days=20)

    # calculate days overdue
    days_overdue = (now_toronto.date() - effective_due_date.date()).days
    # days_overdue = 10

    # It only affects invoices created specifically for this test
    # invoice_metadata = stripe_get(invoice, "metadata", {})

    # is_test_key = (
    #     current_app.config["STRIPE_SECRET_KEY"]
    #     .startswith("sk_test_")
    # )

    # force_overdue_for_test = (
    #     is_test_key
    #     and stripe_get(invoice_metadata, "force_overdue_for_test") == "true"
    # )

    # if force_overdue_for_test:
    #     days_overdue = 1
    
    if days_overdue <= 0:
        return {
            "status": "skipped",
            "reason": "Invoice is not overdue", 
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
            "days_overdue": days_overdue
        }

    # idempotency check
    # check whether a late fee was applied to this invoice within the last 30 days
    if has_recent_late_fee(customer_id, invoice_id):
        return {
            "status": "skipped",
            "reason": "Late fee already applied within last 30 days.",
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
        }

    # idempotency check
    # Normally, block another late fee within 30 days.
    # For a specifically marked Stripe test invoice, allow a second fee
    # so we can verify compounding.

    # invoice_metadata = stripe_get(invoice, "metadata", {})

    # stripe_key = current_app.config["STRIPE_SECRET_KEY"] or ""

    # allow_compounding_test = (
    #     stripe_key.startswith("sk_test_")
    #     and stripe_get(invoice_metadata, "allow_compounding_test") == "true"
    # )

    # if (
    #     has_recent_late_fee(customer_id, invoice_id)
    #     and not allow_compounding_test
    # ):
    #     return {
    #         "status": "skipped",
    #         "reason": "Late fee already applied within last 30 days.",
    #         "invoice_id": invoice_id,
    #         "invoice_number": invoice_number,
    #         "customer_id": customer_id,
    #         "late_fee_month": late_fee_month,
    #     }

    # calculate late fee
    late_fee_cents, base_cents = calculate_compounding_late_fee_cents(invoice)

    # resolve the subscription that should receive the late-fee item
    subscription, subscription_lookup_source = resolve_invoice_subscription(invoice, customer_id)

    if subscription is None:
        return {
            "status": "skipped",
            "reason": (
                "Could not safely resolve exactly one billable subscription for this invoice."
            ),
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
            "subscription_lookup_source": subscription_lookup_source,
        }

    subscription_id = stripe_get(subscription, "id")

    if not subscription_id:
        return {
            "status": "skipped",
            "reason": "Resolved subscription is missing its ID.",
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "customer_id": customer_id,
            "late_fee_month": late_fee_month,
            "subscription_lookup_source": subscription_lookup_source,
        }

    # debug print
    print("INVOICE PERIOD:", invoice_period)

    # create the non-taxable Stripe invoice item
    invoice_item= stripe.InvoiceItem.create(
        customer=customer_id,
        subscription=subscription_id,
        amount=late_fee_cents,
        discountable=False,
        currency="cad",
        # Make it non-taxable
        tax_code="txcd_00000000",
        # simply means the entered amount is treated as the amount before any tax calculation
        tax_behavior="exclusive",
        description=f"Late payment charge (1.5%) - invoice {invoice_number} - {invoice_period}",
        metadata={
            "type" : "late_fee",
            "source_invoice_id" : invoice_id,
            "source_invoice_number": invoice_number,
            "late_fee_month" : late_fee_month,
            "compounding": "true",
            "late_fee_base_cents": str(base_cents),
            "subscription_id": subscription_id,
            "subscription_lookup_source": subscription_lookup_source,
            "accounting_rule_version": "pretax_nontaxable_compounding_v1",
            }
    )

    # return success response
    return {
        "status": "success",
        "customer_id": customer_id,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "invoice_period": invoice_period,
        "late_fee_month": late_fee_month,
        "late_fee": cents_to_money(late_fee_cents), 
        "late_fee_cents": late_fee_cents,
        "late_fee_base": cents_to_money(base_cents),
        "late_fee_base_cents": base_cents,
        "invoice_item_id": invoice_item.id,
        "subscription_id": subscription_id,
        "subscription_lookup_source": subscription_lookup_source,
        "tax_code": "txcd_00000000",
        "tax_behavior": "exclusive",
        "accounting_rule_version": "pretax_nontaxable_compounding_v1",
    }

# invoices often store the subscription under invoice.parent.subscription_details.subscription
def resolve_invoice_subscription(invoice, customer_id): 
    subscription = stripe_get(invoice, "subscription")

    if subscription and not isinstance(subscription, str):
        return subscription, "invoice.subscription_expanded"
    
    if isinstance(subscription, str):
        return stripe.Subscription.retrieve(subscription), "invoice.subscription_id"
    
    parent = stripe_get(invoice, "parent")
    subscription_details = stripe_get(parent, "subscription_details", {}) if parent else {}
    parent_subscription_id = stripe_get(subscription_details, "subscription")

    if parent_subscription_id:
        return stripe.Subscription.retrieve(parent_subscription_id), "invoice.parent.subscription_details"
    
    matches = []
    
    for status in ["active", "past_due", "unpaid"]:
        subs =stripe.Subscription.list(
            customer=customer_id,
            status=status,
            limit=100
        )

        for sub in subs.auto_paging_iter():
            matches.append(sub)

    if len(matches) == 1:
        return matches[0], "customer_has_exactly_one_billable_subscription"

    if len(matches) == 0:
        return None, "no_billable_subscription_found"

    return None, f"multiple_billable_subscriptions_found:{len(matches)}"

# apply late fee for one person before apply to all
@main.route("/admin/apply-late-fee-one/<invoice_id>", methods=["POST"])
def apply_late_fee_one(invoice_id):
    if not logged_in_or_dev():
        return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY": 
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route. "
        }, 400
    
    mode = request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400
    
    run_id= str(uuid.uuid4())

    try:
        result= apply_late_fee_to_invoice(invoice_id)

    except Exception as e:
        result = {
            "status": "failed",
            "invoice_id": invoice_id,
            "error": str(e),
        }

    log= LateFeeLog(
        run_id= run_id,
        invoice_id=result.get("invoice_id"),
        invoice_number=result.get("invoice_number"),
        customer_id=result.get("customer_id"),
        invoice_item_id=result.get("invoice_item_id"),
        late_fee_month=result.get("late_fee_month"),
        amount_cents=result.get("late_fee_cents", 0),
        status=result.get("status"),
        reason=result.get("reason"),
        error=result.get("error"),
        created_at=datetime.now(timezone.utc),
        livemode=is_live_mode(),
    )

    db.session.add(log)
    db.session.commit()

    return result

# Apply late fee to everyone
@main.route("/admin/apply-late-fees", methods=["POST"])
def apply_late_fees():
    if not logged_in_or_dev():
        return redirect("/login")
    
    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route. "
        }, 400
    
    # safety check in test/live mode
    mode= request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required.Submit mode=test or mode=live."
        }, 400
    
    is_live_key= current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400
    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400
    
    run_id= str(uuid.uuid4())

    audit_result= find_late_fee_candidates()

    candidates= audit_result["candidates"]

    results = []

    for candidate in candidates: 
        if not candidate["eligible_to_apply"]:
            results.append({
                "status": "skipped",
                "invoice_id": candidate["invoice_id"],
                "reason": candidate.get("skip_reason")
            })

            log= LateFeeLog(
                run_id= run_id,
                invoice_id= candidate["invoice_id"],
                invoice_number=candidate.get("invoice_number"),
                customer_id= candidate["customer_id"],
                invoice_item_id= None,
                late_fee_month= candidate["late_fee_month"],
                amount_cents= candidate["late_fee_cents"],                
                status= "skipped",
                reason= candidate.get("skip_reason"),
                error= None,
                created_at= datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

            continue

        try:
            result= apply_late_fee_to_invoice(candidate["invoice_id"])
            results.append(result)

            log= LateFeeLog(
                run_id= run_id,
                invoice_id= result.get("invoice_id"),
                invoice_number=result.get("invoice_number"),
                customer_id= result.get("customer_id"),
                invoice_item_id= result.get("invoice_item_id"),
                late_fee_month= candidate["late_fee_month"],
                amount_cents= result.get("late_fee_cents", 0),
                status= result.get("status"),
                reason= result.get("reason"),
                error= None,
                created_at= datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

        except Exception as e:
            results.append({
                "status": "failed", 
                "invoice_id": candidate["invoice_id"],
                "error": str(e)
            })

            # failed log
            log= LateFeeLog(
                run_id= run_id,
                invoice_id= candidate["invoice_id"],
                invoice_number=candidate.get("invoice_number"),
                customer_id= candidate["customer_id"],
                invoice_item_id= None,
                late_fee_month= candidate["late_fee_month"],
                amount_cents= candidate["late_fee_cents"],
                status= "failed",
                reason= None,
                error= str(e),
                created_at= datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

    # commit once after the loop 
    db.session.commit()

    skipped_count= sum(1 for result in results if result["status"] == "skipped")

    redirect_to_dashboard= request.form.get("redirect_to_dashboard")

    if redirect_to_dashboard == "true":
        return redirect("/admin/late-fee-dashboard")

    return {
        "run_id": run_id,
        "mode": mode,
        "is_live_key": is_live_key,
        "status": "completed", 
        "total_candidates": len(candidates),
        "eligible_count": sum(1 for c in candidates if c["eligible_to_apply"]),
        "success_count": sum(1 for r in results if r["status"] == "success"),
        "skipped_count": skipped_count,
        "failed_count": sum(1 for r in results if r["status"] == "failed"),
        "invoice_ids": [c["invoice_id"] for c in candidates],
        "results": results
        # equivalent long version
        # invoice_ids = []
        # for c in candidates:
        #     invoice_ids.append(
        #         c["invoice_id"]
        #     )
    }

# Late fee logs
@main.route("/admin/late-fee-logs")
def late_fee_logs():
    if not session.get("logged_in"):
        return redirect("/login")
    
    logs= LateFeeLog.query.order_by(
        LateFeeLog.created_at.desc()
    ).limit(100).all()

    return render_template(
        "late_fee_logs.html",
        logs=logs,
        total_logs=LateFeeLog.query.count(),
        TORONTO_TZ=TORONTO_TZ,
        timezone=timezone,
    )

    # above is python shorthand
    # log_rows = []

    # for log in logs:
    #     row = {
    #         "id": log.id,
    #         "run_id": log.run_id,
    #         "invoice_id": log.invoice_id,
    #     }

    #     log_rows.append(row)

# idempotency
def carry_forward_already_exists(customer_id, source_invoice_id):
    invoice_items= stripe.InvoiceItem.list(
        customer=customer_id
    )
    for invoice_item in invoice_items.auto_paging_iter():
        metadata= stripe_get(invoice_item, "metadata", {})

        if (
            stripe_get(metadata, "type") == "carry_forward_balance"
            and stripe_get(metadata, "source_invoice_id") == source_invoice_id):
            return True
    return False

# Carry-forward logic:
# Move an old unpaid invoice balance onto a future invoice
# by creating a new pending invoice item for the same customer.

# We void Invoice #1 instead of marking uncollectible because:
# - uncollectible doesn't block payment in Stripe (customer can still pay)
# - void fully disables the payment link preventing double collection
# Tradeoff: void is permanent and may send customer a notification
# Future improvement: webhook guard approach would be more scalable
def carry_forward_invoice_balance(invoice_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    invoice_before = stripe.Invoice.retrieve(invoice_id)
    
    invoice_status= stripe_get(invoice_before, "status")
    amount_remaining= stripe_get(invoice_before, "amount_remaining", 0)
    currency= stripe_get(invoice_before, "currency")
    customer_id= stripe_get(invoice_before, "customer")
    invoice_number= stripe_get(invoice_before, "number")
    old_invoice_status_before = stripe_get(invoice_before, "status")
    source_invoice_created_ts = stripe_get(invoice_before, "created")
    source_invoice_due_date_ts = stripe_get(invoice_before, "due_date")
    source_invoice_total_cents = stripe_get(invoice_before, "total")
    source_invoice_amount_remaining_cents = stripe_get(invoice_before, "amount_remaining", 0)

    carry_forward_description = f"Previous unpaid balance - invoice {invoice_number}"

    base_result = {
        "invoice_id": invoice_id,
        "source_invoice_number": invoice_number,
        "customer_id": customer_id,
        "amount_remaining_cents": amount_remaining,
        "source_invoice_created_ts": source_invoice_created_ts,
        "source_invoice_due_date_ts": source_invoice_due_date_ts,
        "source_invoice_total_cents": source_invoice_total_cents,
        "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
    }

    if invoice_status != "open":
        return {
            "status": "skipped",
            "reason": "Invoice is not open",
            "invoice_id": invoice_id,
            "source_invoice_number": invoice_number,
            "customer_id": customer_id,
            "invoice_status": invoice_status,
            "amount_remaining_cents": amount_remaining,
            "source_invoice_created_ts": source_invoice_created_ts,
            "source_invoice_due_date_ts": source_invoice_due_date_ts,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
        }
    
    if amount_remaining <= 0:
        return {
            "status": "skipped", 
            "reason": "Invoice has no remaining balance",
            "invoice_id": invoice_id,
            "source_invoice_number": invoice_number,
            "customer_id": customer_id,
            "amount_remaining_cents": amount_remaining,
            "source_invoice_created_ts": source_invoice_created_ts,
            "source_invoice_due_date_ts": source_invoice_due_date_ts,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
        }
    
    if currency != "cad":
        return {
            "status": "skipped",
            "reason": "Invoice currency is not CAD",
            "invoice_id": invoice_id,
            "source_invoice_number": invoice_number,
            "customer_id": customer_id,
            "amount_remaining_cents": amount_remaining,
            "currency": currency,
            "source_invoice_created_ts": source_invoice_created_ts,
            "source_invoice_due_date_ts": source_invoice_due_date_ts,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
        }
    
    if not customer_id:
        return {
            "status": "skipped",
            "reason": "Invoice is missing customer",
            "invoice_id": invoice_id
        }
    
    if carry_forward_already_exists(customer_id, invoice_id):
        return {
            **base_result,
            "status": "skipped",
            "reason": "Carry forward already exists",
        }
    
    candidate = get_carry_forward_candidate_by_invoice_id(invoice_id)

    if not candidate:
        return {
            **base_result,
            "status": "skipped",
            "reason": "Invoice is not currently a carry-forward candidate",
        }

    if not candidate["eligible_to_apply"]:
        return {
            **base_result,
            "status": "skipped",
            "reason": candidate["skip_reason"],
            "days_until_next_invoice": candidate.get("days_until_next_invoice"),
            "next_invoice_date": candidate.get("next_invoice_date")
        }

    if not candidate.get("subscription_id"):
        return {
            **base_result,
            "status": "skipped",
            "reason": "Target subscription could not be determined safely",
            "subscription_lookup_source": candidate.get(
                "subscription_lookup_source"
            )
        }

    carry_forward_amount_cents = candidate.get("proposed_carry_forward_amount_cents")

    source_total_excluding_tax_cents = candidate.get("source_total_excluding_tax_cents")

    if source_total_excluding_tax_cents is None:
        return {
            **base_result,
            "status": "skipped",
            "reason": "Source total_excluding_tax is unavailable",
            "source_total_excluding_tax_cents": None,
            "proposed_carry_forward_amount_cents": carry_forward_amount_cents,
        }

    if carry_forward_amount_cents is None:
        return {
            **base_result,
            "status": "skipped", 
            "reason": "Proposed carry-forward amount is unavailable",
            "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "proposed_carry_forward_amount_cents": None,
            }
    
    if carry_forward_amount_cents != source_total_excluding_tax_cents:
        return {
            **base_result,
            "status": "skipped",
            "reason": "Proposed carry-forward amount does not match source total_excluding_tax",
            "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "proposed_carry_forward_amount_cents": carry_forward_amount_cents,
            "manual_action_required": True,
        }
    
    metadata = {
        "type": "carry_forward_balance",
        "source_invoice_id": invoice_id,
        "source_invoice_number": invoice_number or "",
        "target_subscription_id": candidate["subscription_id"],
        "source_total_cents": str(source_invoice_total_cents),
        "source_total_excluding_tax_cents": str(source_total_excluding_tax_cents),
        "source_amount_remaining_cents": str(source_invoice_amount_remaining_cents),
        "accounting_rule_version": "pre_tax_carry_forward_v1",
    }

    try: 
        stripe.Invoice.void_invoice(invoice_id)

        invoice_after= stripe.Invoice.retrieve(invoice_id)
        old_invoice_status_after = stripe_get(invoice_after, "status")

    except Exception as e:
        return {
            **base_result,
            "status": "partial_failure",
            "reason": "Source invoice void or verification failed",
            "old_invoice_status_before": old_invoice_status_before,
            "old_invoice_status_after": None,
            "source_invoice_void_status_uncertain": True,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
            "intended_carry_forward_amount_cents": carry_forward_amount_cents,
            "invoice_item_created": False,
            "manual_review_required": True,
            "error": str(e),
        }

    if old_invoice_status_after != "void":
        return {
            **base_result,
            "status": "failed",
            "reason": "Invoice was not voided, so carry-forward item was not created",
            "old_invoice_status_before": old_invoice_status_before,
            "old_invoice_status_after": old_invoice_status_after,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
            "intended_carry_forward_amount_cents": carry_forward_amount_cents,
        }
    
    try:
        invoice_item= stripe.InvoiceItem.create(
            customer=customer_id, 
            subscription=candidate["subscription_id"],
            # amount_remaining = tax-inclusive old balance
            # carry forward approved pre-tax balance
            amount=carry_forward_amount_cents,
            currency="cad",
            discountable=False,
            tax_behavior="exclusive",
            description=carry_forward_description, 
            metadata=metadata
        )

    except Exception as e:
        return {
            **base_result,
            "status": "partial_failure",
            "reason": "Source invoice was voided but carry-forward item creation failed",
            "source_invoice_number": invoice_number,
            "customer_id": customer_id,
            "old_invoice_status_before": old_invoice_status_before,
            "old_invoice_status_after": old_invoice_status_after,
            "source_invoice_was_voided": True,
            "invoice_item_created": False,
            "source_invoice_total_cents": source_invoice_total_cents,
            "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
            "intended_carry_forward_amount_cents": carry_forward_amount_cents,
            "carry_forward_description": carry_forward_description,
            "manual_action_required": True,
            "requires_carry_forward_item_repair": True,
            "error": str(e),
        }

    created_amount = stripe_get(invoice_item, "amount")

    if created_amount != carry_forward_amount_cents:
        return {
            **base_result,
            "status": "partial_failure",
            "reason": "Carry-forward item was created with an unexpected amount",
            "source_invoice_number": invoice_number,
            "customer_id": customer_id,
            "invoice_item_id": stripe_get(invoice_item, "id"),
            "old_invoice_status_before": old_invoice_status_before,
            "old_invoice_status_after": old_invoice_status_after,
            "source_invoice_was_voided": True,
            "invoice_item_created": True,
            "expected_amount_cents": carry_forward_amount_cents,
            "actual_amount_cents": created_amount,
            "manual_action_required": True,
        }

    return {
        "status": "success",
        "invoice_id": invoice_id,
        "source_invoice_number": invoice_number,
        "new_invoice_id": None,
        "new_invoice_number": None,
        "invoice_number": invoice_number,
        "customer_id": customer_id,
        "amount_remaining_cents": amount_remaining,
        "amount_remaining": cents_to_money(amount_remaining),
        "invoice_item_id": invoice_item.id, 
        "old_invoice_status_before": old_invoice_status_before,
        "old_invoice_status_after": old_invoice_status_after,
        "source_invoice_created_ts": source_invoice_created_ts,
        "source_invoice_due_date_ts": source_invoice_due_date_ts,
        "source_invoice_total_cents": source_invoice_total_cents,
        "source_total_excluding_tax_cents": source_total_excluding_tax_cents,
        "source_invoice_amount_remaining_cents": source_invoice_amount_remaining_cents,
        "carried_forward_amount_cents": carry_forward_amount_cents,
        "carry_forward_description": carry_forward_description,
    }

@main.route("/admin/carry-forward-one/<invoice_id>", methods=["POST"])
def carry_forward_one(invoice_id):
    if not logged_in_or_dev():
        return redirect("/login")

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400
    
    # safety check in test/live mode
    mode= request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required.Submit mode=test or mode=live."
        }, 400
    
    is_live_key= current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400
    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    result = carry_forward_invoice_balance(invoice_id)

    run_id = str(uuid.uuid4())

    if result.get("customer_id"):
        log = create_carry_forward_log_from_result(run_id, result)

        db.session.add(log)
        db.session.commit()

    else:
        result["database_log_created"] = False
        result["database_log_skip_reason"] = (
            "Result has no customer_id, but CarryForwardLog.customer_id is required."
        )

    return result

# Who would be carried forward if we ran today?
@main.route("/admin/audit-carry-forward")
def audit_carry_forward():
    if not logged_in_or_dev():
        return redirect("/login")
    
    return find_carry_forward_candidates()
    # def audit_carry_forward():
    # result = find_carry_forward_candidates()
    # return result
    # the same thing

def classify_carry_forward_invoice(invoice):
    today_toronto = datetime.now(TORONTO_TZ).date()

    invoice_id = stripe_get(invoice, "id")
    amount_remaining = stripe_get(invoice, "amount_remaining")
    currency = stripe_get(invoice, "currency")

    total = stripe_get(invoice, "total")
    total_excluding_tax = stripe_get(invoice, "total_excluding_tax")
    amount_paid = stripe_get(invoice, "amount_paid", 0)
    pre_payment_credit_notes_amount = stripe_get(
        invoice,
        "pre_payment_credit_notes_amount",
        0
    )
    post_payment_credit_notes_amount = stripe_get(
        invoice,
        "post_payment_credit_notes_amount",
        0
    )

    customer = stripe_get(invoice, "customer")
    parent = stripe_get(invoice, "parent")
    subscription = stripe_get(invoice, "subscription")
    due_date_ts = stripe_get(invoice, "due_date")
    created_ts = stripe_get(invoice, "created")

    if currency != "cad":
        return None

    if amount_remaining <= 0:
        return None

    if due_date_ts:
        effective_due_date = datetime.fromtimestamp(
            due_date_ts,
            tz=timezone.utc
        ).astimezone(TORONTO_TZ)
    else:
        effective_due_date = datetime.fromtimestamp(
            created_ts,
            tz=timezone.utc
        ).astimezone(TORONTO_TZ) + timedelta(days=20)

    raw_days = (today_toronto - effective_due_date.date()).days

    if raw_days <= 0:
        return None

    days_overdue = raw_days

    customer_id = (
        stripe_get(customer, "id")
        if not isinstance(customer, str)
        else customer
    )

    # subscription_id = (
    #     stripe_get(subscription, "id")
    #     if not isinstance(subscription, str)
    #     else subscription
    # )

    # parent_subscription_id = None

    # if parent:
    #     parent_subscription_details = stripe_get(
    #         parent,
    #         "subscription_details"
    #     )

    #     if parent_subscription_details:
    #         parent_subscription_id = stripe_get(
    #             parent_subscription_details,
    #             "subscription"
    #         )

    # if not subscription and parent_subscription_id:
    #     subscription = stripe.Subscription.retrieve(
    #         parent_subscription_id
    #     )
    #     subscription_id = stripe_get(subscription, "id")

    # if not subscription:
    #     for status in ["active", "past_due"]:
    #         customer_subscriptions = stripe.Subscription.list(
    #             customer=customer_id,
    #             status=status,
    #             limit=1
    #         )

    #         if customer_subscriptions.data:
    #             subscription = customer_subscriptions.data[0]
    #             subscription_id = stripe_get(subscription, "id")
    #             break

    subscription, subscription_lookup_source = resolve_invoice_subscription(invoice, customer_id)

    subscription_id = stripe_get(subscription, "id") if subscription else None

    next_invoice_date = None
    days_until_next_invoice = None

    if subscription and not isinstance(subscription, str):
        subscription_items = stripe_get(
            stripe_get(subscription, "items", {}),
            "data",
            []
        )

        current_period_end_ts = None

        if subscription_items:
            current_period_end_ts = stripe_get(
                subscription_items[0],
                "current_period_end"
            )

        billing_cycle_anchor_ts = stripe_get(
            subscription,
            "billing_cycle_anchor"
        )

        if current_period_end_ts:
            next_invoice_dt = datetime.fromtimestamp(
                current_period_end_ts,
                tz=timezone.utc
            )
            next_invoice_date = next_invoice_dt.astimezone(
                TORONTO_TZ
            ).date()

        elif billing_cycle_anchor_ts:
            next_invoice_date = get_next_monthly_billing_date_from_anchor(
                billing_cycle_anchor_ts,
                today_toronto
            )

        if next_invoice_date:
            days_until_next_invoice = (
                next_invoice_date - today_toronto
            ).days

    already_exists = carry_forward_already_exists(
        customer_id,
        invoice_id
    )

    fully_unpaid = (
        amount_remaining == total
        and amount_paid == 0
    )

    has_no_credit_notes = (
        pre_payment_credit_notes_amount == 0
        and post_payment_credit_notes_amount == 0
    )

    has_safe_tax_exclusive_amount = (
        total_excluding_tax is not None
        and total_excluding_tax > 0
    )

    safe_accounting_structure = (
        fully_unpaid
        and has_no_credit_notes
        and has_safe_tax_exclusive_amount
    )

    eligible_to_apply = (
        not already_exists
        and days_until_next_invoice == 1
        and safe_accounting_structure
    )

    if already_exists:
        skip_reason = "carry forward already exists"
    elif subscription is None:
        skip_reason = "next invoice date cannot be determined automatically"
    elif isinstance(subscription, str):
        skip_reason = "subscription was not expanded"
    elif days_until_next_invoice is None:
        skip_reason = "next invoice date unavailable"
    elif amount_remaining != total:
        skip_reason = "manual review: amount_remaining does not equal invoice total"
    elif amount_paid != 0:
        skip_reason = "manual review: invoice has a payment applied"
    elif not has_no_credit_notes:
        skip_reason = "manual review: invoice contains credit-note activity"
    elif not has_safe_tax_exclusive_amount:
        skip_reason = "manual review: total_excluding_tax is missing or invalid"
    elif days_until_next_invoice != 1:
        skip_reason = (
            "next invoice is not tomorrow; "
            f"days_until_next_invoice={days_until_next_invoice}"
        )
    else:
        skip_reason = None

    return {
        "invoice_id": invoice_id,
        "customer_id": customer_id,
        "subscription_id": subscription_id,
        "subscription_lookup_source": subscription_lookup_source,
        "next_invoice_date": next_invoice_date.isoformat() if next_invoice_date else None,
        "days_until_next_invoice": days_until_next_invoice,
        "amount_remaining": cents_to_money(amount_remaining),
        "amount_remaining_cents": amount_remaining,
        "effective_due_date": effective_due_date.date().isoformat(),
        "days_overdue": days_overdue,
        "eligible_to_apply": eligible_to_apply,
        "skip_reason": skip_reason,
        "source_total_cents": total,
        "source_total": cents_to_money(total),
        "source_total_excluding_tax_cents": total_excluding_tax,
        "source_total_excluding_tax": (
            cents_to_money(total_excluding_tax)
            if total_excluding_tax is not None else None
        ),
        "source_amount_paid_cents": amount_paid,
        "source_amount_paid": cents_to_money(amount_paid),
        "fully_unpaid": fully_unpaid,
        "has_no_credit_notes": has_no_credit_notes,
        "safe_accounting_structure": safe_accounting_structure,
        "proposed_carry_forward_amount_cents": (
            total_excluding_tax
            if safe_accounting_structure
            else None
        ),
        "proposed_carry_forward_amount": (
            cents_to_money(total_excluding_tax)
            if safe_accounting_structure
            else None
        ),
    }

def find_carry_forward_candidates():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    candidates = []

    invoices = stripe.Invoice.list(
        status="open",
        limit=100,
        expand=["data.customer", "data.subscription"]
    )

    for invoice in invoices.auto_paging_iter():
        candidate = classify_carry_forward_invoice(invoice)

        if candidate:
            candidates.append(candidate)

    return {
        "summary": {
            "candidate_count": len(candidates),
            "eligible_count": sum(
                1 for candidate in candidates
                if candidate["eligible_to_apply"]
            ),
            "skipped_count": sum(
                1 for candidate in candidates
                if not candidate["eligible_to_apply"]
            ),
            "next_invoice_tomorrow_count": sum(
                1 for candidate in candidates
                if candidate["days_until_next_invoice"] == 1
            ),
            "with_next_invoice_date_count": sum(
                1 for candidate in candidates
                if candidate["next_invoice_date"] is not None
            ),
            "missing_next_invoice_date_count": sum(
                1 for candidate in candidates
                if candidate["next_invoice_date"] is None
            ),
        },
        "candidates": candidates
    }

# def find_carry_forward_candidates():
    # stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # candidates = []

    # now= datetime.now(timezone.utc)
    # today_toronto= datetime.now(TORONTO_TZ).date()

    # invoices = stripe.Invoice.list(
    #     status="open",
    #     limit=100,
    #     expand=["data.customer", "data.subscription"]
    # )

    # for invoice in invoices.auto_paging_iter():
    #     invoice_id= stripe_get(invoice, "id")
    #     amount_remaining= stripe_get(invoice, "amount_remaining")
    #     currency= stripe_get(invoice, "currency")

    #     # include/exlude tax revise
    #     total = stripe_get(invoice, "total")
    #     total_excluding_tax = stripe_get(invoice, "total_excluding_tax")
    #     amount_paid = stripe_get(invoice, "amount_paid", 0)
    #     pre_payment_credit_notes_amount = stripe_get(invoice, "pre_payment_credit_notes_amount", 0)
    #     post_payment_credit_notes_amount = stripe_get(invoice, "post_payment_credit_notes_amount", 0)

    #     customer= stripe_get(invoice, "customer")
    #     parent = stripe_get(invoice, "parent")
    #     subscription = stripe_get(invoice, "subscription")
    #     due_date_ts= stripe_get(invoice, "due_date")
    #     created_ts= stripe_get(invoice, "created")

    #     if currency != "cad":
    #         continue

    #     if amount_remaining <= 0:
    #         continue

    #     if due_date_ts:
    #         effective_due_date = datetime.fromtimestamp(due_date_ts, tz=timezone.utc).astimezone(TORONTO_TZ)
    #     else:
    #         effective_due_date = datetime.fromtimestamp(created_ts, tz=timezone.utc).astimezone(TORONTO_TZ) + timedelta(days=20)

    #     raw_days = (today_toronto - effective_due_date.date()).days
    #     # testing
    #     # raw_days = 10

    #     if raw_days <=0:
    #         continue

    #     days_overdue= raw_days

    #     # if condition:
    #     #     use A
    #     # else:
    #     #     use B

    #     # A if condition else B
    #     customer_id= stripe_get(customer, "id") if not isinstance(customer, str) else customer

    #     # carry forward 1 day before the next invoice is generated
    #     subscription_id = stripe_get(subscription, "id") if not isinstance(subscription, str) else subscription

    #     parent_subscription_id = None

    #     if parent:
    #         parent_subscription_details = stripe_get(parent, "subscription_details")

    #         if parent_subscription_details:
    #             parent_subscription_id = stripe_get(parent_subscription_details, "subscription")

    #     if not subscription and parent_subscription_id:
    #         subscription = stripe.Subscription.retrieve(parent_subscription_id)
    #         subscription_id = stripe_get(subscription, "id")

    #     if not subscription:
    #         for status in ["active", "past_due"]:
    #             customer_subscriptions = stripe.Subscription.list(
    #                 customer=customer_id,
    #                 status=status,
    #                 limit=1
    #             )

    #             if customer_subscriptions.data:
    #                 subscription = customer_subscriptions.data[0]
    #                 subscription_id = stripe_get(subscription, "id")
    #                 break

    #     next_invoice_date = None
    #     days_until_next_invoice = None

    #     if subscription and not isinstance(subscription, str):
    #         current_period_end_ts = stripe_get(subscription, "current_period_end")
    #         billing_cycle_anchor_ts = stripe_get(subscription, "billing_cycle_anchor")

    #         if current_period_end_ts:
    #             next_invoice_dt = datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
    #             next_invoice_date = next_invoice_dt.astimezone(TORONTO_TZ).date()

    #         elif billing_cycle_anchor_ts:
    #             next_invoice_date = get_next_monthly_billing_date_from_anchor(
    #                 billing_cycle_anchor_ts,
    #                 today_toronto
    #             )

    #         if next_invoice_date:
    #             days_until_next_invoice = (next_invoice_date - today_toronto).days

    #     already_exists = carry_forward_already_exists(customer_id, invoice_id)

    #     # without tax carry forward
    #     fully_unpaid = (
    #         amount_remaining == total
    #         and amount_paid == 0
    #     )

    #     has_no_credit_notes = (
    #         pre_payment_credit_notes_amount == 0
    #         and post_payment_credit_notes_amount == 0
    #     )

    #     has_safe_tax_exclusive_amount = (
    #         total_excluding_tax is not None
    #         and total_excluding_tax > 0
    #     )

    #     safe_accounting_structure = (
    #         fully_unpaid
    #         and has_no_credit_notes
    #         and has_safe_tax_exclusive_amount
    #     )

    #     eligible_to_apply = (
    #         not already_exists
    #         and days_until_next_invoice == 1
    #         and safe_accounting_structure
    #     )

    #     # for special case: invoice is going to be generated the same day but later time to generate invoice
    #     # eligible_to_apply = (
    #     #     not already_exists
    #     #     and days_until_next_invoice in [0, 1]
    #     # )

    #     if already_exists:
    #         skip_reason = "carry forward already exists"
    #     elif subscription is None:
    #         skip_reason = "next invoice date cannot be determined automatically"
    #     elif isinstance(subscription, str):
    #         skip_reason = "subscription was not expanded"
    #     elif days_until_next_invoice is None:
    #         skip_reason = "next invoice date unavailable"

    #     # Only automate completely unpaid invoices.
    #     elif amount_remaining != total:
    #         skip_reason = "manual review: amount_remaining does not equal invoice total"
    #     # without tax carry forward
    #     elif amount_paid != 0:
    #         skip_reason = "manual review: invoice has a payment applied"
    #     elif not has_no_credit_notes:
    #         skip_reason = "manual review: invoice contains credit-note activity"
    #     elif not has_safe_tax_exclusive_amount:
    #         skip_reason = "manual review: total_excluding_tax is missing or invalid"

    #     elif days_until_next_invoice != 1:
    #         skip_reason = f"next invoice is not tomorrow; days_until_next_invoice={days_until_next_invoice}"
    #     # for special case: invoice is going to be generated the same day but later time to generate invoice
    #     # elif days_until_next_invoice not in [0, 1]:
    #     #     skip_reason = f"next invoice is not today or tomorrow; days_until_next_invoice={days_until_next_invoice}"
    #     else:
    #         skip_reason = None

    #     candidates.append({
    #         "invoice_id": invoice_id,
    #         "customer_id": customer_id,
    #         "subscription_id": subscription_id,
    #         "next_invoice_date": next_invoice_date.isoformat() if next_invoice_date else None,
    #         "days_until_next_invoice": days_until_next_invoice,
    #         "amount_remaining": cents_to_money(amount_remaining),
    #         "amount_remaining_cents": amount_remaining,
    #         "effective_due_date": effective_due_date.date().isoformat(),
    #         "days_overdue": days_overdue,
    #         "eligible_to_apply": eligible_to_apply,
    #         "skip_reason": skip_reason,
    #         # without tax carry forward
    #         "source_total_cents": total,
    #         "source_total": cents_to_money(total),
    #         "source_total_excluding_tax_cents": total_excluding_tax,
    #         "source_total_excluding_tax": (
    #             cents_to_money(total_excluding_tax) if total_excluding_tax is not None else None
    #         ),
    #         "source_amount_paid_cents": amount_paid,
    #         "source_amount_paid": cents_to_money(amount_paid),
    #         "fully_unpaid": fully_unpaid,
    #         "has_no_credit_notes": has_no_credit_notes,
    #         "safe_accounting_structure": safe_accounting_structure,
    #         "proposed_carry_forward_amount_cents": total_excluding_tax if safe_accounting_structure else None,
    #         "proposed_carry_forward_amount": (
    #             cents_to_money(total_excluding_tax) if safe_accounting_structure else None
    #         ),
    #     })

    # return {
    #     "summary": {
    #         "candidate_count": len(candidates),
    #         "eligible_count": sum(1 for c in candidates if c["eligible_to_apply"]),
    #         "skipped_count": sum(1 for c in candidates if not c["eligible_to_apply"]),
    #         "next_invoice_tomorrow_count": sum(1 for c in candidates if c["days_until_next_invoice"] == 1),
    #         "with_next_invoice_date_count": sum(1 for c in candidates if c["next_invoice_date"] is not None),
    #         "missing_next_invoice_date_count": sum(1 for c in candidates if c["next_invoice_date"] is None),
    #     },
    #     "candidates": candidates
    # }

def get_carry_forward_candidate_by_invoice_id(invoice_id):
    # audit_result = find_carry_forward_candidates()

    # for candidate in audit_result["candidates"]:
    #     if candidate["invoice_id"] == invoice_id:
    #         return candidate

    # return None

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    invoice = stripe.Invoice.retrieve(
        invoice_id,
        expand=["customer", "subscription"]
    )

    return classify_carry_forward_invoice(invoice)

# debug-subscription
@main.route("/admin/debug-subscription/<subscription_id>")
def debug_subscription(subscription_id):
    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    sub = stripe.Subscription.retrieve(subscription_id)

    items= stripe_get(sub, "items", {})
    subscription_items = stripe_get(items, "data", [])

    return {
        "subscription_id": stripe_get(sub, "id"),
        "status": stripe_get(sub, "status"),
        "current_period_start": stripe_get(sub, "current_period_start"),
        "current_period_end": stripe_get(sub, "current_period_end"),
        "billing_cycle_anchor": stripe_get(sub, "billing_cycle_anchor"),
        "schedule": stripe_get(sub, "schedule"),
        "cancel_at": stripe_get(sub, "cancel_at"),
        "cancel_at_period_end": stripe_get(sub, "cancel_at_period_end", False),
        "collection_method": stripe_get(sub, "collection_method"),
        "items_count": len(subscription_items),
        "metadata": stripe_metadata_to_dict(stripe_get(sub, "metadata", {}) or {}),
    }

def get_next_monthly_billing_date_from_anchor(anchor_ts, today_date):
    anchor_dt = datetime.fromtimestamp(anchor_ts, tz=timezone.utc).astimezone(TORONTO_TZ)

    def safe_date_for_month(year, month):
        last_day= calendar.monthrange(year, month)[1]
        day= min(anchor_dt.day, last_day)
        
        return date(year, month, day)

    year = today_date.year
    month = today_date.month

    candidate = safe_date_for_month(year, month)

    # <   means move forward only when the candidate is before today
    # <=  means move forward when the candidate is before today or is today

    # If today is the billing day, the next invoice is next month, not today.
    if candidate <= today_date:
        if month == 12:
            candidate = safe_date_for_month(year + 1, 1)
        else:
            candidate = safe_date_for_month(year, month + 1)

    return candidate

# debug invoice
@main.route("/admin/debug-carry-forward-invoice/<invoice_id>")
def debug_carry_forward_invoice(invoice_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    invoice = stripe.Invoice.retrieve(invoice_id)
    parent = stripe_get(invoice, "parent")
    subscription_details = stripe_get(invoice, "subscription_details")
    

    return {
        "id": stripe_get(invoice, "id"),
        "created": stripe_get(invoice, "created"),
        "due_date": stripe_get(invoice, "due_date"),
        "subscription": stripe_get(invoice, "subscription"),
        "parent_type": type(parent).__name__ if parent else None,
        "parent_str": str(parent) if parent else None,
        "subscription_details_type": type(subscription_details).__name__ if subscription_details else None,
        "subscription_details_str": str(subscription_details) if subscription_details else None,
        "period_start": stripe_get(invoice, "period_start"),
        "period_end": stripe_get(invoice, "period_end"),
        "status": stripe_get(invoice, "status"),
        "collection_method": stripe_get(invoice, "collection_method")
    }

# debug accounting and tax composition
@main.route("/admin/debug-invoice-accounting/<invoice_id>", methods=["GET"])
def debug_invoice_accounting(invoice_id):
    """
    Read-only accounting and tax inspection for one Stripe invoice.

    This route does not create, modify, void, pay, or finalize anything.
    """

    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    try: 
        invoice = stripe.Invoice.retrieve(
            invoice_id,
            expand=["customer", "lines.data.price.product"]
            )
        
    except stripe.error.StripeError as error:
        return {
            "status": "failed",
            "read_only": True,
            "invoice_id": invoice_id,
            "error": str(error),
        }, 400

    customer = stripe_get(invoice, "customer")

    if isinstance(customer, str):
        customer_id = customer
        customer_name = None
        customer_email = None

    else: 
        customer_id = stripe_get(customer, "id")
        customer_name = stripe_get(customer, "name")
        customer_email = stripe_get(customer, "email")

    total_tax_amounts = []

    for tax_amount in stripe_get(invoice, "total_tax_amounts", []) or []:
        tax_rate_reference = stripe_get(tax_amount, "tax_rate")

        if isinstance(tax_rate_reference, str):
            tax_rate_id = tax_rate_reference
            tax_rate_percentage = None
            tax_rate_display_name = None

        else: 
            tax_rate_id = stripe_get(tax_rate_reference, "id")
            tax_rate_percentage = stripe_get(tax_rate_reference, "percentage")
            tax_rate_display_name = stripe_get(tax_rate_reference, "display_name")

        total_tax_amounts.append({
            "amount_cents": stripe_get(tax_amount, "amount", 0),
            "amount": cents_to_money(stripe_get(tax_amount, "amount", 0)),
            "inclusive": stripe_get(tax_amount, "inclusive"),
            "tax_rate_id": tax_rate_id,
            "tax_rate_percentage": tax_rate_percentage,
            "tax_rate_display_name": tax_rate_display_name,
            "taxability_reason": stripe_get(tax_amount, "taxability_reason"),
        })

    line_results = []

    lines = stripe_get(invoice, "lines", {})
    line_data = stripe_get(lines, "data", []) or []

    for line in line_data:
        line_metadata = stripe_metadata_to_dict(stripe_get(line, "metadata", {}) or {})

        price = stripe_get(line, "price", {}) or {}
        price_id = stripe_get(price, "id")
        tax_behavior = stripe_get(price, "tax_behavior")
        product_reference = stripe_get(price, "product")

        if isinstance(product_reference, str): 
            product_id = product_reference
            product_name = None
            product_tax_code = None

        else:
            product_id = stripe_get(product_reference, "id")
            product_name = stripe_get(product_reference, "name")
            tax_code_reference = stripe_get(product_reference, "tax_code")

            if isinstance(tax_code_reference, str):
                product_tax_code = tax_code_reference
            else:
                product_tax_code = stripe_get(tax_code_reference, "id")

        line_tax_amounts = []

        for tax_amount in stripe_get(line, "tax_amounts", []) or []:
            tax_rate_reference = stripe_get(tax_amount, "tax_rate")

            if isinstance(tax_rate_reference, str):
                tax_rate_id = tax_rate_reference
                tax_rate_percentage = None
                tax_rate_display_name = None

            else:
                tax_rate_id = stripe_get(tax_rate_reference, "id")
                tax_rate_percentage = stripe_get(tax_rate_reference, "percentage")
                tax_rate_display_name = stripe_get(tax_rate_reference, "display_name")

            line_tax_amounts.append({
                "amount_cents": stripe_get(tax_amount, "amount", 0),
                "amount": cents_to_money(stripe_get(tax_amount, "amount", 0)),
                "inclusive": stripe_get(tax_amount, "inclusive"),
                "tax_rate_id": tax_rate_id,
                "tax_rate_percentage": tax_rate_percentage,
                "tax_rate_display_name": tax_rate_display_name,
                "taxability_reason": stripe_get(tax_amount, "taxability_reason"),
            })

        amount_excluding_tax = stripe_get(line, "amount_excluding_tax")

        line_results.append({
            "line_id": stripe_get(line, "id"),
            "type": stripe_get(line, "type"),
            "description": stripe_get(line, "description"),
            "amount_cents": stripe_get(line, "amount", 0),
            "amount": cents_to_money(stripe_get(line, "amount", 0)),
            "amount_excluding_tax_cents": amount_excluding_tax,
            "amount_excluding_tax": cents_to_money(amount_excluding_tax) if amount_excluding_tax is not None else None,
            "currency": stripe_get(line, "currency"),
            "quantity": stripe_get(line, "quantity"),
            "invoice_item_id": stripe_get(line, "invoice_item"),
            "subscription_item_id": stripe_get(line, "subscription_item"),
            "price_id": price_id,
            "price_tax_behavior": tax_behavior,
            "product_id": product_id,
            "product_name": product_name,
            "product_tax_code": product_tax_code,
            "metadata": line_metadata,
            "tax_amounts": line_tax_amounts,
            "taxes_raw": stripe_value_to_plain_python(stripe_get(line, "taxes", []) or []),
            "discount_amounts": stripe_value_to_plain_python(stripe_get(line, "discount_amounts", []) or []),
            "period": stripe_value_to_plain_python(stripe_get(line, "period")),
            "parent_raw": stripe_value_to_plain_python(stripe_get(line, "parent")),
            "pricing_raw": stripe_value_to_plain_python(stripe_get(line, "pricing")),
        })

    total_taxes_raw = stripe_get(invoice, "total_taxes", []) or []
    calculated_tax_cents = 0

    for tax_entry in total_taxes_raw:
        calculated_tax_cents += stripe_get(tax_entry, "amount", 0)

    return {
        "status": "success",
        "read_only": True,
        "invoice": {
            "invoice_id": stripe_get(invoice, "id"),
            "invoice_number": stripe_get(invoice, "number"),
            "status": stripe_get(invoice, "status"),
            "currency": stripe_get(invoice, "currency"),
            "collection_method": stripe_get(invoice, "collection_method"),
            "automatic_tax": stripe_value_to_plain_python(stripe_get(invoice, "automatic_tax")),
            "subtotal_cents": stripe_get(invoice, "subtotal", 0),
            "subtotal": cents_to_money(stripe_get(invoice, "subtotal", 0)),

            "subtotal_excluding_tax_cents": stripe_get(invoice, "subtotal_excluding_tax"),
            "subtotal_excluding_tax": (
                cents_to_money(stripe_get(invoice, "subtotal_excluding_tax" )) 
                if stripe_get(invoice, "subtotal_excluding_tax") is not None else None
            ),

            "total_cents": stripe_get(invoice, "total", 0),
            "total": cents_to_money(stripe_get(invoice, "total", 0)),

            "total_excluding_tax_cents": stripe_get(invoice, "total_excluding_tax"),
            "total_excluding_tax": (
                cents_to_money(stripe_get(invoice, "total_excluding_tax"))
                if stripe_get(invoice, "total_excluding_tax") is not None else None
            ),

            "legacy_tax_field_cents": stripe_get(invoice, "tax", 0),
            "legacy_tax_field": cents_to_money(stripe_get(invoice, "tax", 0)),

            "calculated_tax_cents": calculated_tax_cents,
            "calculated_tax": cents_to_money(calculated_tax_cents),

            "amount_due_cents": stripe_get(invoice, "amount_due", 0),
            "amount_due": cents_to_money(stripe_get(invoice, "amount_due", 0)),

            "amount_paid_cents": stripe_get(invoice, "amount_paid", 0),
            "amount_paid": cents_to_money(stripe_get(invoice, "amount_paid", 0)),

            "amount_remaining_cents": stripe_get(invoice, "amount_remaining", 0),
            "amount_remaining": cents_to_money(stripe_get(invoice, "amount_remaining", 0) ),

            "starting_balance_cents": stripe_get(invoice, "starting_balance", 0),
            "starting_balance": cents_to_money(stripe_get(invoice, "starting_balance", 0)),

            "ending_balance_cents": stripe_get(invoice, "ending_balance", 0),
            "ending_balance": cents_to_money(stripe_get(invoice, "ending_balance", 0)),

            "pre_payment_credit_notes_amount_cents": stripe_get(invoice, "pre_payment_credit_notes_amount", 0),
            "post_payment_credit_notes_amount_cents": stripe_get(invoice, "post_payment_credit_notes_amount", 0),

            "total_discount_amounts": stripe_value_to_plain_python(stripe_get(invoice, "total_discount_amounts", []) or []),

            "total_tax_amounts": total_tax_amounts,
            "total_taxes_raw": stripe_value_to_plain_python(stripe_get(invoice, "total_taxes", []) or []),
            "created": stripe_get(invoice, "created"),
            "due_date": stripe_get(invoice, "due_date"),
        },

        "customer": {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
        },

        "line_count": len(line_results),
        "lines": line_results,
    }

# debug customer
@main.route("/admin/debug-customer/<customer_id>")
def debug_customer(customer_id):

    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    customer = stripe.Customer.retrieve(customer_id)

    subscriptions = stripe.Subscription.list(
        customer=customer_id,
        status="all",
        limit=100
    )

    return {
        "customer_id": stripe_get(customer, "id"),
        "customer_name": stripe_get(customer, "name"),
        "customer_email": stripe_get(customer, "email"),

        "subscription_count": len(subscriptions.data),

        "subscriptions": [
            {
                "id": stripe_get(sub, "id"),
                "status": stripe_get(sub, "status"),
                "billing_cycle_anchor": stripe_get(sub, "billing_cycle_anchor"),
                "current_period_start": stripe_get(sub, "current_period_start"),
                "current_period_end": stripe_get(sub, "current_period_end"),
                "cancel_at_period_end": stripe_get(sub, "cancel_at_period_end"),
            }
            for sub in subscriptions.data
        ]
    }

# bulk apply carry forward
@main.route("/admin/apply-carry-forwards", methods=["POST"])
def apply_carry_forwards():
    if not logged_in_or_dev():
        return redirect("/login")

    confirm= request.form.get("confirm")

    if confirm != "APPLY": 
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        },400

    # safety check in test/live mode
    mode= request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required.Submit mode=test or mode=live."
        }, 400
    
    is_live_key= current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400
    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    max_apply_raw = request.form.get("max_apply", "1")

    try:
        max_apply = int(max_apply_raw)
    except ValueError:
        return {
            "error": "max_apply must be a whole number."
        }, 400

    if max_apply < 1 or max_apply > 100:
        return {
            "error": "max_apply must be between 1 and 100."
        }, 400
    
    # uuid.uuid4() returns a UUID object.  not a string
    run_id= str(uuid.uuid4())

    audit_result= find_carry_forward_candidates()
    candidates= audit_result["candidates"]

    # test-only invoice filter
    target_invoice_ids_raw = request.form.get("invoice_ids")

    if target_invoice_ids_raw:
        if mode != "test":
            return {
                "error": "invoice_ids filtering is only allowed in test mode."
            }, 400

        target_invoice_ids = {
            invoice_id.strip()
            for invoice_id in target_invoice_ids_raw.split(",")
            if invoice_id.strip()
        }

        targeted_candidates = []

        for target_invoice_id in target_invoice_ids:
            candidate = get_carry_forward_candidate_by_invoice_id(
                target_invoice_id
            )

            if candidate:
                targeted_candidates.append(candidate)

        candidates = targeted_candidates

    results= []

    attempted_count = 0

    for candidate in candidates:
        if not candidate["eligible_to_apply"]:
            results.append({
                "status": "skipped",
                "invoice_id": candidate["invoice_id"],
                "reason": candidate["skip_reason"]
            })

            # log= CarryForwardLog(
            #     run_id= run_id,
            #     invoice_id= candidate["invoice_id"],
            #     customer_id= candidate["customer_id"],
            #     invoice_item_id= None,
            #     amount_cents= candidate["amount_remaining_cents"],
            #     status= "skipped",
            #     old_invoice_status = None,
            #     reason= candidate["skip_reason"],
            #     error= None,
            # )

            # db.session.add(log)

            continue

        if attempted_count >= max_apply:
            results.append({
                "status": "not_attempted",
                "invoice_id": candidate["invoice_id"],
                "reason": "max_apply limit reached"
            })
            continue

        attempted_count += 1

        try:
            result= carry_forward_invoice_balance(candidate["invoice_id"])
            results.append(result)

            log= create_carry_forward_log_from_result(run_id, result)

            db.session.add(log)


        except Exception as e:
            results.append({
                "status": "failed",
                "invoice_id": candidate["invoice_id"],
                "error": str(e)
            })

            log= CarryForwardLog(
                    run_id= run_id,
                    invoice_id= candidate["invoice_id"],
                    customer_id= candidate["customer_id"],
                    invoice_item_id= None,
                    amount_cents= candidate["amount_remaining_cents"],
                    status= "failed",
                    old_invoice_status = None,
                    reason= None,
                    error= str(e),
                    livemode=is_live_mode(),
                )
            
            db.session.add(log)

    db.session.commit()

    return {
        "run_id": run_id,
        "mode": mode,
        "max_apply": max_apply,
        "status": "completed",
        "total_candidates": len(candidates),
        "attempted_count": attempted_count,
        "results": results,
        "eligible_count": sum(1 for c in candidates if c["eligible_to_apply"]),
        "success_count": sum(1 for r in results if r["status"] == "success"),
        "skipped_count": sum(1 for r in results if r["status"] == "skipped"),
        "failed_count": sum(1 for r in results if r["status"] == "failed"),
        "partial_failure_count": sum(1 for r in results if r["status"] == "partial_failure"),
        "not_attempted_count": sum(1 for r in results if r["status"] == "not_attempted"),
    }

# admin carry forward log
@main.route("/admin/carry-forward-logs")
def carry_forward_logs(): 
    if not session.get("logged_in"):
        return redirect("/login")
    
    logs= CarryForwardLog.query.order_by(
        CarryForwardLog.created_at.desc()
    ).limit(100).all()

    return render_template(
        "carry_forward_logs.html", 
        logs=logs,
        total_logs=CarryForwardLog.query.count(),
        TORONTO_TZ=TORONTO_TZ,
        timezone=timezone,
    )

# time format helper
def format_toronto(dt):
    if not dt:
        return None
    
    return dt.astimezone(TORONTO_TZ).strftime("%Y-%m-%d %I:%M:%S %p %Z")

# generate CSV report
@main.route("/admin/accounting-carry-forward-report.csv")
def accounting_carry_forward_report_csv():
    data= get_accounting_carry_forward_report_data()

    output= io.StringIO()
    writer= csv.writer(output)

    # headers
    writer.writerow([
        "Carry Forward Date",
        "Run ID",
        "Customer ID",
        "Source Invoice ID",
        "Source Invoice Number",
        "Source Invoice Total Including Tax",
        "Source Invoice Total Excluding Tax",
        "Source Invoice Amount Remaining",
        "Carried Forward Amount",
        "Pre-Tax Difference Check",
        "Status Before",
        "Status After",
        "Invoice Item ID",
        "Description",
        "Legacy",
    ])

    # rows
    for row in data["rows"]:
        writer.writerow([
            row["carry_forward_date"],
            row["run_id"],
            row["customer_id"],
            row["source_invoice_id"],
            row["source_invoice_number"],
            f"{row['source_invoice_total']:.2f}",
            f"{row['source_invoice_total_excluding_tax']:.2f}" if row["source_invoice_total_excluding_tax"] is not None else "",
            f"{row['source_invoice_amount_remaining']:.2f}",
            f"{row['carried_forward_amount']:.2f}",
            f"{row['difference_check']:.2f}" if row["difference_check"] is not None else "",
            row["status_before"],
            row["status_after"],
            row["invoice_item_id"],
            row["description"],
            "Legacy" if row["is_legacy"] else "Current",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=carry_forward_report.csv"
        }
    )

# accounting helper
def get_accounting_carry_forward_report_data():
    logs= (
        CarryForwardLog.query
        .filter_by(status="success")
        .order_by(CarryForwardLog.created_at.desc())
        .all()
    )

    total_carry_forward = 0
    affected_customers= set()
    rows= []
    largest_amount = 0
    smallest_amount = None

    for log in logs: 
        affected_customers.add(log.customer_id)

        created_at= log.created_at

        amount_cents = log.carried_forward_amount_cents or log.amount_cents or 0
        total_carry_forward += amount_cents

        if amount_cents > largest_amount:
            largest_amount = amount_cents

        if smallest_amount is None or amount_cents < smallest_amount:
            smallest_amount = amount_cents

        source_invoice_number = log.source_invoice_number or log.invoice_id
        status_before = log.old_invoice_status_before or "unknown"
        status_after = log.old_invoice_status_after or log.old_invoice_status
        is_legacy = (
            log.source_invoice_total_excluding_tax_cents is None
            or log.carried_forward_amount_cents is None
        )

        source_total_excluding_tax_cents = log.source_invoice_total_excluding_tax_cents

        if source_total_excluding_tax_cents is not None:
            difference_check_cents = (source_total_excluding_tax_cents - amount_cents)
        else:
            # Old logs created before the pre-tax column existed
            difference_check_cents = None

        rows.append({
            "carry_forward_date": format_toronto(created_at) if created_at else None,
            "run_id": log.run_id,
            "customer_id": log.customer_id,
            "source_invoice_id": log.invoice_id,
            "source_invoice_number": source_invoice_number,
            "source_invoice_created": format_toronto(log.source_invoice_created) if log.source_invoice_created else None,
            "source_invoice_due_date": format_toronto(log.source_invoice_due_date) if log.source_invoice_due_date else None,
            "source_invoice_total_cents": log.source_invoice_total_cents,
            "source_invoice_total": cents_to_money(log.source_invoice_total_cents or 0),
            "source_invoice_total_excluding_tax_cents": source_total_excluding_tax_cents,
            "source_invoice_total_excluding_tax": (
                cents_to_money(source_total_excluding_tax_cents)
                if source_total_excluding_tax_cents is not None else None
            ),
            "source_invoice_amount_remaining_cents": log.source_invoice_amount_remaining_cents,
            "source_invoice_amount_remaining": cents_to_money(log.source_invoice_amount_remaining_cents or 0),
            "carried_forward_amount_cents": amount_cents,
            "carried_forward_amount": cents_to_money(amount_cents),
            "status_before": status_before,
            "status_after": status_after,
            "difference_check_cents": difference_check_cents,
            "difference_check": cents_to_money(difference_check_cents) if difference_check_cents is not None else None,
            "invoice_item_id": log.invoice_item_id,
            "new_invoice_id": log.new_invoice_id,
            "new_invoice_number": log.new_invoice_number,
            "description": log.carry_forward_description,
            "is_legacy": is_legacy,
        })

    invoice_count= len(logs)
    customer_count= len(affected_customers)
    generated_at= datetime.now(TORONTO_TZ)

    return {
        "summary": {
            "report_type": "Carry Forward Reconciliation Report",
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "status_filter": "success",
            "total_carried_forward_cents": total_carry_forward,
            "total_carried_forward": cents_to_money(total_carry_forward),
            "invoice_count": invoice_count,
            "customer_count": customer_count,
            "row_count": len(rows),
            "largest_carry_forward": cents_to_money(largest_amount),
            "smallest_carry_forward": cents_to_money(smallest_amount or 0),
            "average_carry_forward": cents_to_money(total_carry_forward // invoice_count) if invoice_count else 0,
        },
        "rows": rows
    }

# accounting-carry-forward-report
@main.route("/admin/accounting-carry-forward-report")
def accounting_carry_forward_report():
    return get_accounting_carry_forward_report_data()

# html accounting carry forward report page
@main.route("/admin/accounting-carry-forward-report-page")
def accounting_carry_forward_report_page():
    data= get_accounting_carry_forward_report_data()

    return render_template(
        "accounting_carry_forward_report.html",
        data=data
    )

# billing increase log
@main.route("/admin/billing-increase-logs")
def billing_increase_logs():
    if not session.get("logged_in"):
        return redirect("/login")
    
    logs= BillingIncreaseLog.query.order_by(
        BillingIncreaseLog.created_at.desc()
    ).limit(100).all()

    return render_template(
        "billing_increase_logs.html",
        logs=logs, 
        total_logs=BillingIncreaseLog.query.count(),
        TORONTO_TZ=TORONTO_TZ,
        timezone=timezone, 
    )

# combine the overdue processes into a single automated workflow
@main.route("/admin/run-overdue-billing", methods=["POST"])
def run_overdue_billing():
    if not logged_in_or_dev():
        return redirect("/login")

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    mode = request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    run_id = str(uuid.uuid4())

    # STEP 1: apply late fees first
    late_fee_audit = find_late_fee_candidates()
    late_fee_candidates = late_fee_audit["candidates"]

    late_fee_results = []

    for candidate in late_fee_candidates:
        if not candidate["eligible_to_apply"]:
            late_fee_results.append({
                "status": "skipped",
                "invoice_id": candidate["invoice_id"],
                "reason": candidate.get("skip_reason")
            })

            log = LateFeeLog(
                run_id=run_id,
                invoice_id=candidate["invoice_id"],
                invoice_number=candidate.get("invoice_number"),
                customer_id=candidate["customer_id"],
                invoice_item_id=None,
                late_fee_month=candidate["late_fee_month"],
                amount_cents=candidate["late_fee_cents"],
                status="skipped",
                reason=candidate.get("skip_reason"),
                error=None,
                created_at=datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

            continue

        try:
            result = apply_late_fee_to_invoice(candidate["invoice_id"])
            late_fee_results.append(result)

            log = LateFeeLog(
                run_id=run_id,
                invoice_id=result.get("invoice_id"),
                invoice_number=result.get("invoice_number"),
                customer_id=result.get("customer_id"),
                invoice_item_id=result.get("invoice_item_id"),
                late_fee_month=result.get("late_fee_month"),
                amount_cents=result.get("late_fee_cents", 0),
                status=result.get("status"),
                reason=result.get("reason"),
                error=result.get("error"),
                created_at=datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

        except Exception as e:
            late_fee_results.append({
                "status": "failed",
                "invoice_id": candidate["invoice_id"],
                "error": str(e)
            })

            log = LateFeeLog(
                run_id=run_id,
                invoice_id=candidate["invoice_id"],
                invoice_number=candidate.get("invoice_number"),
                customer_id=candidate["customer_id"],
                invoice_item_id=None,
                late_fee_month=candidate["late_fee_month"],
                amount_cents=candidate["late_fee_cents"],
                status="failed",
                reason=None,
                error=str(e),
                created_at=datetime.now(timezone.utc),
                livemode=is_live_mode(),
            )

            db.session.add(log)

    db.session.commit()

    # STEP 2: carry forward after late fees
    carry_forward_audit = find_carry_forward_candidates()
    carry_forward_candidates = carry_forward_audit["candidates"]

    carry_forward_results = []

    for candidate in carry_forward_candidates:
        if not candidate["eligible_to_apply"]:
            carry_forward_results.append({
                "status": "skipped",
                "invoice_id": candidate["invoice_id"],
                "reason": candidate.get("skip_reason")
            })

            # log = CarryForwardLog(
            #     run_id=run_id,
            #     invoice_id=candidate["invoice_id"],
            #     customer_id=candidate["customer_id"],
            #     invoice_item_id=None,
            #     amount_cents=candidate["amount_remaining_cents"],
            #     status="skipped",
            #     old_invoice_status=None,
            #     reason=candidate.get("skip_reason"),
            #     error=None,
            # )

            # db.session.add(log)

            continue

        try:
            result = carry_forward_invoice_balance(candidate["invoice_id"])
            carry_forward_results.append(result)

            log = create_carry_forward_log_from_result(run_id, result)

            db.session.add(log)

        except Exception as e:
            carry_forward_results.append({
                "status": "failed",
                "invoice_id": candidate["invoice_id"],
                "error": str(e)
            })

            log = CarryForwardLog(
                run_id=run_id,
                invoice_id=candidate["invoice_id"],
                customer_id=candidate["customer_id"],
                invoice_item_id=None,
                amount_cents=candidate["amount_remaining_cents"],
                status="failed",
                old_invoice_status=None,
                reason=None,
                error=str(e),
                livemode=is_live_mode(),
            )
            db.session.add(log)

    db.session.commit()

    return {
        "run_id": run_id,
        "mode": mode,
        "is_live_key": is_live_key,
        "status": "completed",

        "late_fees": {
            "total_candidates": len(late_fee_candidates),
            "eligible_count": sum(1 for c in late_fee_candidates if c["eligible_to_apply"]),
            "success_count": sum(1 for r in late_fee_results if r["status"] == "success"),
            "skipped_count": sum(1 for r in late_fee_results if r["status"] == "skipped"),
            "failed_count": sum(1 for r in late_fee_results if r["status"] == "failed"),
            "results": late_fee_results,
        },

        "carry_forwards": {
            "total_candidates": len(carry_forward_candidates),
            "eligible_count": sum(1 for c in carry_forward_candidates if c["eligible_to_apply"]),
            "success_count": sum(1 for r in carry_forward_results if r["status"] == "success"),
            "skipped_count": sum(1 for r in carry_forward_results if r["status"] == "skipped"),
            "failed_count": sum(1 for r in carry_forward_results if r["status"] == "failed"),
            "partial_failure_count": sum(1 for result in carry_forward_results if result["status"] == "partial_failure"),
            "results": carry_forward_results,
        },
    }

# audit due dates thats more than 20 days
@main.route("/admin/audit-invoice-due-dates")
def audit_invoice_due_dates(): 
    if not session.get("logged_in"):
        return redirect("/login")
    
    invoices= stripe.Invoice.list(
        status="open",
        limit=100
    )

    results = []

    # 3. loop through invoices
    for invoice in invoices.auto_paging_iter():
        # debugging
        # pprint(list(invoice._data.keys()))
        # return {"debug": "printed on invoice keys"}

        # 4. get created timestamp
        created_ts = stripe_get(invoice, "created")
        # 5. get due_date timestamp
        due_ts = stripe_get(invoice, "due_date")
        # 6. skip if no created timestamp
        if not created_ts:
            continue
        # 7. skip if no due_date
        # because no due_date means fallback rule applies later
        if not due_ts:
            continue
        # 8. convert timestamps to dates
        created_date = datetime.fromtimestamp(created_ts, tz=timezone.utc).date()
        due_date = datetime.fromtimestamp(due_ts, tz=timezone.utc).date()
        # 9. calculate days_between
        days_between = (due_date - created_date).days

        parent= stripe_get(invoice, "parent", {})
        subscription_details= stripe_get(parent, "subscription_details", {})
        subscription_id= stripe_get(subscription_details, "subscription")

        # 10. if days_between is NOT 20:
        # append invoice info to results
        if days_between != 20:
            results.append({
                "invoice_id": stripe_get(invoice, "id"),
                "invoice_number": stripe_get(invoice, "number"),
                "status": stripe_get(invoice, "status"),
                "created_date": created_date.isoformat(),
                "due_date": due_date.isoformat(),
                "days_between": days_between,
                "customer_id": stripe_get(invoice, "customer"),
                "subscription_id": subscription_id,
                "collection_method": stripe_get(invoice, "collection_method"),
            })

    # 11. return JSON summary
    return {
        "expected_due_days": 20,
        "wrong_due_date_count": len(results),
        "results": results
    }

# debug stripe object (not guessing)
@main.route("/debug-invoice")
def debug_invoice(): 
    invoices= stripe.Invoice.list(limit=1)

    invoice= invoices.data[0]

    return invoice._to_dict_recursive()

# get_late_fee_dashboard_data
def get_late_fee_dashboard_data():
    audit_result = find_late_fee_candidates()

    run_logs = []

    latest_log= LateFeeLog.query.order_by(
        LateFeeLog.created_at.desc()
    ).first()

    if latest_log:
        run_id= latest_log.run_id
    
        run_logs= LateFeeLog.query.filter_by(
            run_id=run_id
        ).all()

    total_amount_cents= 0

    for log in run_logs: 
        if log.status == "success":
            total_amount_cents += log.amount_cents or 0

    last_run_time = None
    last_run_time_toronto = None
    ran_today = False
    
    # 5. calculate:
    success_count= sum(1 for r in run_logs if r.status == "success")
    failed_count= sum(1 for r in run_logs if r.status == "failed")
    skipped_count= sum(1 for r in run_logs if r.status == "skipped")
    
    if latest_log:
        last_run_time = latest_log.created_at

        if last_run_time.tzinfo is None:
            last_run_time = last_run_time.replace(tzinfo=timezone.utc)

        last_run_time_toronto = last_run_time.astimezone(TORONTO_TZ)

        now_toronto = datetime.now(TORONTO_TZ).date()

        if last_run_time_toronto.date() == now_toronto:
            ran_today = True
        else:
            ran_today = False

    recent_logs= []

    for log in run_logs:
        created_at= log.created_at

        if created_at.tzinfo is None:
            created_at= created_at.replace(tzinfo=timezone.utc)

        created_at_toronto= created_at.astimezone(TORONTO_TZ)

        recent_logs.append({
            "created_at": created_at_toronto.strftime("%Y-%m-%d %I:%M %p"),
            "status": log.status,
            "invoice_id": log.invoice_id,
            "invoice_number": log.invoice_number or log.invoice_id,
            "invoice_item_id": log.invoice_item_id,
            "amount": f"${cents_to_money(log.amount_cents):.2f}",
            "reason_or_error": log.reason or log.error or "-"
        })

    return {
        "audit": audit_result,        
        "today_status": {
            "ran_today": ran_today,
            "last_run_time": last_run_time_toronto.strftime("%Y-%m-%d %I:%M %p") if last_run_time_toronto else None,
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "total_amount": f"${cents_to_money(total_amount_cents):.2f}",
        },
        "recent_logs": recent_logs,
    }

@main.route("/admin/late-fee-dashboard")
def late_fee_dashboard():
    if not session.get("logged_in"):
        return redirect("/login")
    
    data= get_late_fee_dashboard_data()

    return render_template(
        "late_fee_dashboard.html",
        data=data
    )

# -----------------------------------------inspection fee ------------------------------------------

# helper
def stripe_metadata_to_dict(metadata):
    """
    Safely convert Stripe metadata into a normal Python dictionary.

    This helper does not modify Stripe.
    """
    if not metadata:
        return {}

    result = {}

    raw_data = getattr(metadata, "_data", None)

    if raw_data:
        for key, value in raw_data.items():
            result[key] = value

    return result

def date_to_str(dt):
    if not dt:
        return None
    
    return dt.date().isoformat()

def days_between(date1, date2):
    if not date1 or not date2:
        return None
    
    return abs((date1.date() - date2.date()).days)

def get_effective_cancel_datetime(subscription):
    cancel_at = stripe_get(subscription, "cancel_at")

    if cancel_at:
        return stripe_timestamp_to_utc_datetime(cancel_at), "subscription.cancel_at"

    schedule = stripe_get(subscription, "schedule")

    if schedule:
        if isinstance(schedule, str):
            schedule = stripe.SubscriptionSchedule.retrieve(schedule)

        if stripe_get(schedule, "end_behavior") == "cancel":
            phases = stripe_get(schedule, "phases", [])
            last_phase = phases[-1] if phases else None

            if last_phase and stripe_get(last_phase, "end_date"):
                return stripe_timestamp_to_utc_datetime(
                    stripe_get(last_phase, "end_date")
                ), "subscription_schedule.last_phase.end_date"

    return None, "missing"

# Check every required metadata key and return a list of the ones that are absent or empty
def get_missing_inspection_metadata_keys(metadata):
    missing_keys = []

    for key in REQUIRED_INSPECTION_METADATA_KEYS:
        if not metadata.get(key):
            missing_keys.append(key)

    return missing_keys

def normalize_audit_text(value):
    """
    Convert a value into normalized lowercase text for audit comparisons.
    "Annual Inspection Fee"  ->  "annual inspection fee"
    """

    if value is None:
        return ""

    return str(value).strip().lower()

def classify_inspection_fee_item(
    product_name,
    price_nickname,
    price_lookup_key,
    item_metadata,
    product_metadata,
):
    """
    Classify a Stripe subscription item using conservative audit rules.

    This is discovery logic only.

    It must not be reused later for automatically deleting subscription
    items until Product IDs or Price IDs have been manually confirmed.
    """

    item_metadata = item_metadata or {}
    product_metadata = product_metadata or {}

    inspection_evidence = []
    main_service_evidence = []

    # Normalize important text fields.
    normalized_product_name = normalize_audit_text(product_name)
    normalized_price_nickname = normalize_audit_text(price_nickname)
    normalized_lookup_key = normalize_audit_text(price_lookup_key)

    # Metadata keys that may describe the type of fee.
    possible_type_keys = {
        "type",
        "fee_type",
        "billing_type",
        "item_type",
        "category",
        "service_type",
    }

    inspection_metadata_values = {
        "inspection",
        "inspection_fee",
        "annual_inspection",
        "annual_inspection_fee",
    }

    main_service_metadata_values = {
        "main_service",
        "main_service_fee",
        "monthly_service",
        "monthly_service_fee",
        "service_fee",
    }

    # Check subscription-item metadata.
    for key, value in item_metadata.items():
        normalized_key = normalize_audit_text(key)
        normalized_value = normalize_audit_text(value)

        if normalized_key in possible_type_keys:
            if normalized_value in inspection_metadata_values:
                inspection_evidence.append(
                    f"item_metadata:{key}={value}"
                )

            if normalized_value in main_service_metadata_values:
                main_service_evidence.append(
                    f"item_metadata:{key}={value}"
                )

    # Check product metadata.
    for key, value in product_metadata.items():
        normalized_key = normalize_audit_text(key)
        normalized_value = normalize_audit_text(value)

        if normalized_key in possible_type_keys:
            if normalized_value in inspection_metadata_values:
                inspection_evidence.append(
                    f"product_metadata:{key}={value}"
                )

            if normalized_value in main_service_metadata_values:
                main_service_evidence.append(
                    f"product_metadata:{key}={value}"
                )

    # Product or Price names containing "inspection" are useful evidence
    # during discovery.
    if "inspection" in normalized_product_name:
        inspection_evidence.append(
            f"product_name:{product_name}"
        )

    if "inspection" in normalized_price_nickname:
        inspection_evidence.append(
            f"price_nickname:{price_nickname}"
        )

    if "inspection" in normalized_lookup_key:
        inspection_evidence.append(
            f"price_lookup_key:{price_lookup_key}"
        )

    # Only use clear main-service phrases here.
    clear_main_service_phrases = {
        "main service",
        "monthly service",
        "service fee",
        "monthly fee",
    }

    for phrase in clear_main_service_phrases:
        if phrase in normalized_product_name:
            main_service_evidence.append(
                f"product_name:{product_name}"
            )
            break

    for phrase in clear_main_service_phrases:
        if phrase in normalized_price_nickname:
            main_service_evidence.append(
                f"price_nickname:{price_nickname}"
            )
            break

    # Conflicting evidence is deliberately classified as ambiguous.
    if inspection_evidence and main_service_evidence:
        classification = "ambiguous"

    elif inspection_evidence:
        classification = "inspection_fee"

    elif main_service_evidence:
        classification = "main_service_fee"

    else:
        classification = "unknown"

    return {
        "classification": classification,
        "inspection_evidence": inspection_evidence,
        "main_service_evidence": main_service_evidence,
    }

INSPECTION_BILLABLE_STATUSES = [
    "active",
    "past_due",
    "unpaid",
]

REQUIRED_INSPECTION_METADATA_KEYS = [
    "contract_start_date",
    "contract_end_date",
    "contract_term_years",
    "inspection_fee_start_date",
    "inspection_fee_end_date",
    "inspection_fee_years",
    "inspection_fee_status",
    "billing_rule_version",
]

# audit inspection fee
@main.route("/admin/audit-inspection-fees")
def audit_inspection_fees(): 

    subscriptions= stripe.Subscription.list(
        status="active",
        limit=100,
    )

#     for subscription in subscriptions.auto_paging_iter(): 
#         items= stripe_get(subscription, "items", {})
#         subscription_items= stripe_get(items, "data",[])

#         for item in subscription_items:
#             price= stripe_get(item, "price", {})
#             product= stripe_get(price, "product")

#             print("----------------------------------")
#             print("Subscription: ", stripe_get(subscription, "id"))
#             print("Item:", stripe_get(item, "id"))
#             print("product: ", product)

#     return {"status": "ok"}
    results = []

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = stripe_get(subscription, "id")
        customer_id = stripe_get(subscription, "customer")
        metadata = stripe_metadata_to_dict(
            stripe_get(subscription, "metadata", {}) or {}
        )

        start_date = stripe_timestamp_to_utc_datetime(
            stripe_get(subscription, "start_date")
        )

        cancel_at = stripe_get(subscription, "cancel_at")
        cancel_at_period_end = stripe_get(subscription, "cancel_at_period_end")

        cancel_at_date = stripe_timestamp_to_utc_datetime(cancel_at)

        items = stripe_get(subscription, "items", {})
        subscription_items = stripe_get(items, "data", [])

        service_items = []
        inspection_items = []
        unknown_items = []

        for item in subscription_items:
            item_id = stripe_get(item, "id")
            item_metadata = stripe_metadata_to_dict(
                stripe_get(item, "metadata", {}) or {}
            )

            price = stripe_get(item, "price", {})
            price_id = stripe_get(price, "id")
            unit_amount = stripe_get(price, "unit_amount")
            currency = stripe_get(price, "currency")

            product_id = stripe_get(price, "product")

            product = stripe.Product.retrieve(product_id) if product_id else {}
            product_name = stripe_get(product, "name", "")

            item_info = {
                "subscription_item_id": item_id,
                "price_id": price_id,
                "product_id": product_id,
                "product_name": product_name,
                "unit_amount": unit_amount,
                "currency": currency,
                "metadata": item_metadata,
            }

            product_name_lower = product_name.lower()
            item_type = item_metadata.get("item_type")

            if item_type == "inspection_fee" or "inspect" in product_name_lower:
                inspection_items.append(item_info)

            elif item_type == "main_service_fee" or "geothermal" in product_name_lower:
                service_items.append(item_info)

            else:
                unknown_items.append(item_info)

        warnings = []

        if cancel_at:
            warnings.append(
                "Subscription has cancel_at set. This may stop the whole 50-year contract."
            )

        if cancel_at_period_end:
            warnings.append(
                "Subscription has cancel_at_period_end=True. This may stop the whole subscription."
            )

        if len(service_items) == 0:
            warnings.append("No main service item found.")

        if len(inspection_items) == 0:
            warnings.append("No inspection fee item found.")

        if len(inspection_items) > 1:
            warnings.append("More than one inspection fee item found.")

        if not stripe_get(metadata, "contract_start_date"):
            warnings.append("Missing metadata: contract_start_date")

        if not stripe_get(metadata, "contract_end_date"):
            warnings.append("Missing metadata: contract_end_date")

        if not stripe_get(metadata, "inspection_fee_end_date"):
            warnings.append("Missing metadata: inspection_fee_end_date")

        results.append({
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "start_date": start_date.date().isoformat() if start_date else None,
            "cancel_at": cancel_at,
            "cancel_at_date": cancel_at_date.date().isoformat() if cancel_at_date else None,
            "cancel_at_period_end": cancel_at_period_end,
            "metadata": metadata,
            "service_items": service_items,
            "inspection_items": inspection_items,
            "unknown_items": unknown_items,
            "warnings": warnings,
        })

    return {
        "count": len(results),
        "results": results,
    }

# Show me exactly what I'm about to do before I actually touch customer subscriptions
@main.route("/admin/preview-inspection-metadata")
def preview_inspection_metadata():
    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100,
        expand=["data.customer"]
    )

    results = []

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = stripe_get(subscription, "id")
        customer_id = stripe_get(subscription, "customer")

        customer = stripe_get(subscription, "customer")

        contract_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(customer, "created")
        )

        if not contract_start_dt:
            results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "action": "skipped",
                "reason": "Missing customer.created date",
            })
            continue

        inspection_end_dt = contract_start_dt + relativedelta(years=3)
        contract_end_dt = contract_start_dt + relativedelta(years=50)

        preview_metadata = {
            "contract_start_date": date_to_str(contract_start_dt),
            "contract_end_date": date_to_str(contract_end_dt),
            "contract_term_years": "50",
            "inspection_fee_start_date": date_to_str(contract_start_dt),
            "inspection_fee_end_date": date_to_str(inspection_end_dt),
            "inspection_fee_years": "3",
            "inspection_fee_status": "active",
            "billing_rule_version": "1",
        }

        results.append({
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "current_cancel_at": stripe_get(subscription, "cancel_at"),
            "current_cancel_at_date": date_to_str(
                stripe_timestamp_to_utc_datetime(
                    stripe_get(subscription, "cancel_at")
                )
            ),
            "would_add_metadata": preview_metadata,
            "action": "preview_only_no_changes",
        })

    return {
        "count": len(results),
        "results": results,
    }

# summary of preview for weird results
@main.route("/admin/preview-inspection-metadata-summary")
def preview_inspection_metadata_summary():
    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100,
        expand=["data.customer", "data.schedule"]
    )

    total_checked = 0
    normal_count = 0
    weird_results = []

    for subscription in subscriptions.auto_paging_iter():
        total_checked += 1

        subscription_id = stripe_get(subscription, "id")
        customer_id = stripe_get(subscription, "customer")

        customer = stripe_get(subscription, "customer")

        contract_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(customer, "created")
        )

        cancel_dt, cancel_source = get_effective_cancel_datetime(subscription)

        if not contract_start_dt:
            weird_results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "reason": "missing_customer_created_date",
            })
            continue

        expected_inspection_end_dt = contract_start_dt + relativedelta(years=3)
        expected_contract_end_dt = contract_start_dt + relativedelta(years=50)

        weird_reasons = []

        if not cancel_dt:
            weird_reasons.append("cancel_at_is_missing")

        else:
            difference_days = days_between(cancel_dt, expected_inspection_end_dt)

            if difference_days > 1:
                weird_reasons.append(
                    f"cancel_at_does_not_match_3_year_inspection_end_date_by_{difference_days}_days"
                )

        if expected_contract_end_dt.year - contract_start_dt.year != 50:
            weird_reasons.append("contract_end_date_not_50_years_after_start")

        if weird_reasons:
            weird_results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "start_date": date_to_str(contract_start_dt),
                "current_cancel_at_date": date_to_str(cancel_dt),
                "expected_inspection_fee_end_date": date_to_str(expected_inspection_end_dt),
                "expected_contract_end_date": date_to_str(expected_contract_end_dt),
                "weird_reasons": weird_reasons,
            })
        else:
            normal_count += 1

    return {
        "total_checked": total_checked,
        "normal_count": normal_count,
        "weird_count": len(weird_results),
        "weird_results": weird_results,
    }

# categorize weird cases
@main.route("/admin/preview-inspection-weird-categories")
def preview_inspection_weird_categories():
    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100,
        expand=["data.customer", "data.schedule"]
    )

    summary = {
        "total_checked": 0,
        "normal_count": 0,
        "missing_cancel_at_count": 0,
        "early_cancel_count": 0,
        "late_cancel_count": 0,
        "twenty_year_cancel_count": 0,
    }

    examples = {
        "missing_cancel_at": [],
        "early_cancel": [],
        "late_cancel": [],
        "twenty_year_cancel": [],
    }

    for subscription in subscriptions.auto_paging_iter():
        summary["total_checked"] += 1

        subscription_id = stripe_get(subscription, "id")
        customer_id = stripe_get(subscription, "customer")

        customer = stripe_get(subscription, "customer")

        contract_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(customer, "created")
        )

        cancel_dt, cancel_source = get_effective_cancel_datetime(subscription)

        if not contract_start_dt:
            continue

        expected_inspection_end_dt = contract_start_dt + relativedelta(years=3)
        expected_contract_end_dt = contract_start_dt + relativedelta(years=50)

        base_info = {
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "start_date": date_to_str(contract_start_dt),
            "current_cancel_at_date": date_to_str(cancel_dt),
            "expected_inspection_fee_end_date": date_to_str(expected_inspection_end_dt),
            "expected_contract_end_date": date_to_str(expected_contract_end_dt),
        }

        if not cancel_dt:
            summary["missing_cancel_at_count"] += 1

            if len(examples["missing_cancel_at"]) < 10:
                examples["missing_cancel_at"].append(base_info)

            continue

        difference_days = (cancel_dt.date() - expected_inspection_end_dt.date()).days

        if abs(difference_days) <= 1:
            summary["normal_count"] += 1
            continue

        years_from_start = cancel_dt.year - contract_start_dt.year

        if years_from_start == 20:
            summary["twenty_year_cancel_count"] += 1

            if len(examples["twenty_year_cancel"]) < 10:
                examples["twenty_year_cancel"].append({
                    **base_info,
                    "difference_days": difference_days,
                    "years_from_start": years_from_start,
                })

        elif difference_days < -1:
            summary["early_cancel_count"] += 1

            if len(examples["early_cancel"]) < 10:
                examples["early_cancel"].append({
                    **base_info,
                    "difference_days": difference_days,
                })

        elif difference_days > 1:
            summary["late_cancel_count"] += 1

            if len(examples["late_cancel"]) < 10:
                examples["late_cancel"].append({
                    **base_info,
                    "difference_days": difference_days,
                })

    return {
        "summary": summary,
        "examples": examples,
    }

# CSV report of weird cases
@main.route("/admin/export-inspection-weird-cases") 
def export_inspection_weird_cases():
    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100,
        expand=["data.customer", "data.schedule"]
    )

    rows = []

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = stripe_get(subscription, "id")
        
        subscription_start_dt = stripe_timestamp_to_utc_datetime(stripe_get(subscription, "start_date"))

        customer = stripe_get(subscription, "customer")

        # Business rule: contract start date is based on Stripe Customer.created ("Customer Since")
        contract_start_dt = stripe_timestamp_to_utc_datetime(stripe_get(customer, "created"))
        contract_cancel_dt, cancel_source = get_effective_cancel_datetime(subscription)

        customer_id = stripe_get(customer, "id")
        customer_name = stripe_get(customer, "name")
        customer_email = stripe_get(customer, "email")

        if not contract_start_dt:
            row = {
                "Customer Name": customer_name,
                "Customer Email": customer_email,
                "Category": "missing_customer_created_date",
                "Customer ID": customer_id,
                "Subscription ID": subscription_id,
                "Customer Since": None,
                "Subscription Start Date": subscription_start_dt.date().isoformat() if subscription_start_dt else None,
                "Current Cancel Date": contract_cancel_dt.date().isoformat() if contract_cancel_dt else None,
                "Cancel Source": cancel_source,
                "Expected Inspection End Date": None,
                "Expected Contract End Date": None,
                "Days Difference": None,
                "Recommended Action": "Customer created date is missing. Review manually.",
                "Boss Decision": "",
                "Notes": "",
            }

            rows.append(row)
            continue

        expected_inspection_end_dt = contract_start_dt + relativedelta(years=3)
        expected_contract_end_dt = contract_start_dt + relativedelta(years=50)

        category = None
        recommended_action = None
        days_difference = None

        if not contract_cancel_dt:
            category = "missing_cancel_at"
            recommended_action = "No cancel date is currently set. Review only. "

        else:
            days_difference = (contract_cancel_dt.date() - expected_inspection_end_dt.date()).days
            years_from_start = contract_cancel_dt.year - contract_start_dt.year

            # 0 days off, 1 day early, 1 day late all considered close enough because dates in Stripe are sometimes weird
            if abs(days_difference) <= 1:
            # abs(5) = 5, abs(-5) = 5
            # it removes the sign
                continue
                # This subscription is normal. Skip it. Don't put it in the report

            elif years_from_start == 20:
                category = "twenty_year_cancel"
                recommended_action = "Subscription appears set to cancel after 20 years. Confirm business rule."

            # more than 1 day EARLY
            elif days_difference < -1:
                category = "early_cancel"
                recommended_action = "Subscription may end too early. Needs review."

            # more than 1 day LATE
            elif days_difference > 1: 
                category = "late_cancel"
                recommended_action = "Inspection fee may continue too long. Needs review."

        row = {
            "Customer Name": customer_name,
            "Customer Email": customer_email,
            "Category": category,
            "Customer ID": customer_id,
            "Subscription ID": subscription_id,
            "Customer Since": contract_start_dt.date().isoformat() if contract_start_dt else None,
            "Subscription Start Date": subscription_start_dt.date().isoformat() if subscription_start_dt else None,
            "Current Cancel Date": contract_cancel_dt.date().isoformat() if contract_cancel_dt else None,
            "Cancel Source": cancel_source,
            "Expected Inspection End Date": expected_inspection_end_dt.date().isoformat() if expected_inspection_end_dt else None,
            "Expected Contract End Date": expected_contract_end_dt.date().isoformat() if expected_contract_end_dt else None,
            "Days Difference": days_difference,
            "Recommended Action": recommended_action,
            "Boss Decision": "",
            "Notes": "",
        }

        rows.append(row)

    output = io.StringIO()

    fieldnames = [
        "Customer Name",
        "Customer Email",
        "Category",
        "Customer ID",
        "Subscription ID",
        "Customer Since",
        "Subscription Start Date",
        "Current Cancel Date",
        "Cancel Source",
        "Expected Inspection End Date",
        "Expected Contract End Date",
        "Days Difference",
        "Recommended Action",
        "Boss Decision",
        "Notes",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=inspection_weird_cases.csv"
        },
    )

# debug-subscription-lifecycle
@main.route("/admin/debug-subscription-lifecycle/<subscription_id>")
def debug_subscription_lifecycle(subscription_id):
    subscription = stripe.Subscription.retrieve(
        subscription_id,
        expand=["customer", "schedule"]
    )

    schedule = stripe_get(subscription, "schedule")

    return {
        "subscription_id": stripe_get(subscription, "id"),
        "customer_id": stripe_get(stripe_get(subscription, "customer"), "id"),
        "customer_created": stripe_get(stripe_get(subscription, "customer"), "created"),
        "subscription_start_date": stripe_get(subscription, "start_date"),
        "subscription_cancel_at": stripe_get(subscription, "cancel_at"),
        "subscription_cancel_at_period_end": stripe_get(subscription, "cancel_at_period_end"),
        "subscription_status": stripe_get(subscription, "status"),
        "subscription_schedule": stripe_get(schedule, "id") if schedule else None,
        "schedule_status": stripe_get(schedule, "status") if schedule else None,
        "schedule_end_behavior": stripe_get(schedule, "end_behavior") if schedule else None,
        "schedule_phases": [
            {
                "start_date": stripe_timestamp_to_utc_datetime(stripe_get(phase, "start_date")).date().isoformat()
                if stripe_get(phase, "start_date") else None,

                "end_date": stripe_timestamp_to_utc_datetime(stripe_get(phase, "end_date")).date().isoformat()
                if stripe_get(phase, "end_date") else None,
            }
            for phase in stripe_get(schedule, "phases", [])
        ] if schedule else [],
    }

# preview-inspection-metadata-apply
@main.route("/admin/preview-inspection-metadata-apply")
def preview_inspection_metadata_apply():
    subscriptions = stripe.Subscription.list(
        status="active",
        limit=100,
        expand=["data.customer", "data.schedule"]
    )

    target_subscription_id = request.args.get("subscription_id")

    results = []

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = stripe_get(subscription, "id")

        if target_subscription_id and subscription_id != target_subscription_id:
            continue

        customer= stripe_get(subscription, "customer")
        customer_id = stripe_get(customer, "id")
        current_metadata = stripe_metadata_to_dict(stripe_get(subscription, "metadata", {}) or {})

        contract_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(customer, "created")
        )

        subscription_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(subscription, "start_date")
        )

        cancel_dt, cancel_source = get_effective_cancel_datetime(subscription)

        if not contract_start_dt:
            results.append({
                "Category": "missing_customer_created_date",
                "Customer ID": customer_id,
                "Subscription ID": subscription_id,
                "Start Date": None,
                "Current Cancel Date": cancel_dt.date().isoformat() if cancel_dt else None,
                "Expected Inspection End Date": None,
                "Expected Contract End Date": None,
                "Days Difference": None,
                "Recommended Action": "Customer created date is missing. Review manually.",
                "Boss Decision": "",
                "Notes": "",
            })

            continue

        expected_inspection_end_dt = contract_start_dt + relativedelta(years=3)
        expected_contract_end_dt = contract_start_dt + relativedelta(years=50)

        would_add_metadata = {
            "contract_start_date": contract_start_dt.date().isoformat(),
            "contract_end_date": expected_contract_end_dt.date().isoformat(),
            "contract_term_years": "50",
            "inspection_fee_start_date": contract_start_dt.date().isoformat(),
            "inspection_fee_end_date": expected_inspection_end_dt.date().isoformat(),
            "inspection_fee_years": "3",
            "inspection_fee_status": "active",
            "billing_rule_version": "1",
        }

        results.append({
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "customer_since": contract_start_dt.date().isoformat(),
            "subscription_start_date": subscription_start_dt.date().isoformat() if subscription_start_dt else None,
            "current_cancel_date": cancel_dt.date().isoformat() if cancel_dt else None,
            "cancel_source": cancel_source,
            "current_metadata": current_metadata,
            "would_add_metadata": would_add_metadata,
            "action": "preview_only_no_changes",
        })

    return {
        "count": len(results),
        "results": results[:30],
    }

# apply metadata for inspection fee
@main.route("/admin/apply-inspection-metadata", methods=["POST"])
def apply_inspection_metadata():
    if not logged_in_or_dev():
        return redirect("/login")

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    mode = request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400
    
    # Specify subscription ID from terminal
    target_subscription_id = request.form.get("subscription_id")
    
    subscriptions= stripe.Subscription.list(
        status="active", 
        limit=100,
        expand=["data.customer"],
    )

    results = []

    # for saving to the CSV file
    log_rows = []

    updated_count = 0
    failed_count = 0

    # to generate CSV report 
    # Create a folder called logs. If it already exists, don’t crash.
    os.makedirs("logs", exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_filename = f"inspection_metadata_apply_log_{timestamp}.csv"
    # Put these path pieces together correctly -> logs\abc.csv   (it avoids manually choosing / or \)
    log_path= os.path.join("logs", log_filename)

    for subscription in subscriptions.auto_paging_iter():
        subscription_id = stripe_get(subscription, "id")

        # # apply for one person only before bulk apply
        # TEST_SUBSCRIPTION_ID = "sub_1TThLQE2kujhweZxBGs9Qoij"

        # # Ignore this subscription and continue with the rest of the subscriptions
        # if subscription_id != TEST_SUBSCRIPTION_ID:
        #     continue
        #     # continue: Skip everything below me in THIS iteration, then start the next iteration.

        if target_subscription_id and subscription_id != target_subscription_id:
            continue

        customer = stripe_get(subscription, "customer")
        customer_id = stripe_get(customer, "id")

        contract_start_dt = stripe_timestamp_to_utc_datetime(
            stripe_get(customer, "created")
        )

        if not contract_start_dt:
            raise ValueError(
                f"Subscription {subscription_id} is missing created date. Stop and investigate before applying metadata."
            )

        current_metadata = stripe_metadata_to_dict(stripe_get(subscription, "metadata", {}) or {})

        expected_inspection_end_dt = contract_start_dt + relativedelta(years=3)
        expected_contract_end_dt = contract_start_dt + relativedelta(years=50)

        new_metadata = {
            # The ** means: Take every key/value pair from this dictionary and put it into the new dictionary
            **current_metadata,
            "contract_start_date": contract_start_dt.date().isoformat(),
            "contract_end_date": expected_contract_end_dt.date().isoformat(),
            "contract_term_years": "50",
            "inspection_fee_start_date": contract_start_dt.date().isoformat(),
            "inspection_fee_end_date": expected_inspection_end_dt.date().isoformat(),
            "inspection_fee_years": "3",
            "inspection_fee_status": "active",
            "billing_rule_version": "1",
        }

        try: 
            stripe.Subscription.modify(
                subscription_id,
                metadata=new_metadata,
            )

            updated_count += 1

            results.append({
                "subscription_id" : subscription_id,
                "customer_id": customer_id,
                "action": "metadata_updated",
                "metadata": new_metadata,
            })

            log_rows.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "action": "metadata_updated",
                "reason": "",
                "error": "",
                "contract_start_date": contract_start_dt.date().isoformat(),
                "contract_end_date": expected_contract_end_dt.date().isoformat(),
                "contract_term_years": new_metadata["contract_term_years"],
                "inspection_fee_start_date": new_metadata["inspection_fee_start_date"],
                "inspection_fee_end_date": new_metadata["inspection_fee_end_date"],
                "inspection_fee_years": new_metadata["inspection_fee_years"],
                "inspection_fee_status": new_metadata["inspection_fee_status"],
                "billing_rule_version": new_metadata["billing_rule_version"],            
            })
        
        except Exception as e: 
            failed_count += 1

            results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "action": "failed",
                "error": str(e),
            })

            log_rows.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "action": "failed",
                "error": str(e),
                "reason": "",
                "contract_start_date": contract_start_dt.date().isoformat(),
                "contract_end_date": expected_contract_end_dt.date().isoformat(),
                "contract_term_years": new_metadata["contract_term_years"],
                "inspection_fee_start_date": new_metadata["inspection_fee_start_date"],
                "inspection_fee_end_date": new_metadata["inspection_fee_end_date"],
                "inspection_fee_years": new_metadata["inspection_fee_years"],
                "inspection_fee_status": new_metadata["inspection_fee_status"],
                "billing_rule_version": new_metadata["billing_rule_version"],   
            })

            # I'm done with this failed subscription. Go to the next subscription
            continue

    fieldnames = [
        "subscription_id",
        "customer_id",
        "action",
        "reason",
        "error",
        "contract_start_date",
        "contract_end_date",
        "contract_term_years",
        "inspection_fee_start_date",
        "inspection_fee_end_date",
        "inspection_fee_years",
        "inspection_fee_status",
        "billing_rule_version",
    ]

    # Open this CSV file for writing. Let me write into it. When I’m done, close it automatically. "w" means write mode.
    with open(log_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(log_rows)

    return {
        "status": "ok",
        "updated_count": updated_count,
        "failed_count": failed_count,
        "results": results,
        "log_file": log_path
    }

# building Audit which active/past_due/unpaid subscriptions are missing inspection metadata
@main.route("/admin/audit-inspection-metadata-coverage")
def audit_inspection_metadata_coverage():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    billable_statuses = [
        "active",
        "past_due",
        "unpaid",
    ]

    required_metadata_keys = [
        "contract_start_date",
        "contract_end_date",
        "contract_term_years",
        "inspection_fee_start_date",
        "inspection_fee_end_date",
        "inspection_fee_years",
        "inspection_fee_status",
        "billing_rule_version",
    ]

    summary = {
        "checked" : 0,
        "complete_count" : 0,
        "missing_metadata_count" : 0,
        "status_counts" : {
            "active" : 0,
            "past_due" : 0,
            "unpaid" : 0,
        },
    }

    missing_results = []

    for status in billable_statuses:
        subscriptions =stripe.Subscription.list(
            status= status,
            limit=100,
        )

        for subscription in subscriptions.auto_paging_iter():
            summary["checked"] += 1
            summary["status_counts"][status] += 1

            metadata = stripe_metadata_to_dict(stripe_get(subscription, "metadata", {}) or {})

            missing_keys = []

            for key in required_metadata_keys: 
                if not metadata.get(key):
                    missing_keys.append(key)

            if missing_keys:
                summary["missing_metadata_count"] += 1

                missing_results.append({
                    "subscription_id": stripe_get(subscription, "id"),
                    "customer_id": stripe_get(subscription, "customer"),
                    "status": stripe_get(subscription, "status"),
                    "missing_keys": missing_keys,
                    "current_metadata": metadata,
                })

            else:
                summary["complete_count"] += 1

    return {
        "summary" : summary,
        "missing_results" : missing_results,
    }

# investigate the four active subscriptions so we know why they were missed
@main.route("/admin/debug-missing-inspection-metadata-active")
def debug_missing_inspection_metadata_active():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    subscription_ids = [
        "sub_1TTnDHE2kujhweZxsJOp2viU",
        "sub_1SACWKE2kujhweZxyHi6aCwU",
        "sub_1RvHSbE2kujhweZxWhe1XR7r",
        "sub_1PY7MyE2kujhweZxWBAmzpHU",
    ]

    results = []

    for subscription_id in subscription_ids:
        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["customer", "latest_invoice"]
        )

        customer = stripe_get(subscription, "customer")
        latest_invoice = stripe_get(subscription, "latest_invoice")
        metadata = stripe_metadata_to_dict(
            stripe_get(subscription, "metadata", {}) or {}
        )

        results.append({
            "subscription_id": subscription_id,
            "subscription_status": stripe_get(subscription, "status"),
            "subscription_created": stripe_timestamp_to_utc_datetime(
                stripe_get(subscription, "created")
            ).isoformat() if stripe_get(subscription, "created") else None,

            "customer_id": stripe_get(customer, "id"),
            "customer_email": stripe_get(customer, "email"),

            "latest_invoice_id": stripe_get(latest_invoice, "id"),
            "latest_invoice_status": stripe_get(latest_invoice, "status"),
            "latest_invoice_paid": stripe_get(latest_invoice, "paid"),
            "latest_invoice_amount_remaining": stripe_get(
                latest_invoice,
                "amount_remaining"
            ),

            "current_metadata": metadata,
        })

    return {
        "count": len(results),
        "results": results,
    }

# repair only active/past_due/unpaid subscriptions that are still missing metadata
@main.route("/admin/preview-missing-inspection-metadata-apply")
def preview_missing_inspection_metadata_apply():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    target_subscription_id = request.args.get("subscription_id")

    results = []

    checked_count = 0
    missing_count = 0
    complete_skipped_count = 0
    unsafe_count = 0

    for status in INSPECTION_BILLABLE_STATUSES:
        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100,
            expand=["data.customer", "data.schedule"],
        )

        for subscription in subscriptions.auto_paging_iter():
            subscription_id = stripe_get(subscription, "id")

            if (
                target_subscription_id
                and subscription_id != target_subscription_id
            ):
                continue

            checked_count += 1

            customer = stripe_get(subscription, "customer")
            customer_id = stripe_get(customer, "id")

            current_metadata = stripe_metadata_to_dict(
                stripe_get(subscription, "metadata", {}) or {}
            )

            missing_keys = get_missing_inspection_metadata_keys(
                current_metadata
            )

            if not missing_keys:
                complete_skipped_count += 1
                continue

            missing_count += 1

            contract_start_dt = stripe_timestamp_to_utc_datetime(
                stripe_get(customer, "created")
            )

            if not contract_start_dt:
                unsafe_count += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "unsafe_skip",
                    "reason": "missing_customer_created_date",
                    "missing_keys": missing_keys,
                    "current_metadata": current_metadata,
                })

                continue

            expected_inspection_end_dt = (
                contract_start_dt + relativedelta(years=3)
            )

            expected_contract_end_dt = (
                contract_start_dt + relativedelta(years=50)
            )

            metadata_to_merge = {
                "contract_start_date": (
                    contract_start_dt.date().isoformat()
                ),
                "contract_end_date": (
                    expected_contract_end_dt.date().isoformat()
                ),
                "contract_term_years": "50",
                "inspection_fee_start_date": (
                    contract_start_dt.date().isoformat()
                ),
                "inspection_fee_end_date": (
                    expected_inspection_end_dt.date().isoformat()
                ),
                "inspection_fee_years": "3",
                "inspection_fee_status": "active",
                "billing_rule_version": "1",
            }

            final_metadata_after_merge = {
                **current_metadata,
                **metadata_to_merge,
            }

            results.append({
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "subscription_status": stripe_get(
                    subscription,
                    "status"
                ),
                "missing_keys": missing_keys,
                "current_metadata": current_metadata,
                "would_add_metadata": metadata_to_merge,
                "final_metadata_after_merge": (
                    final_metadata_after_merge
                ),
                "action": "preview_only_no_changes",
            })

    return {
        "summary": {
            "statuses_checked": INSPECTION_BILLABLE_STATUSES,
            "checked_count": checked_count,
            "missing_count": missing_count,
            "complete_skipped_count": complete_skipped_count,
            "unsafe_count": unsafe_count,
            "preview_result_count": len(results),
        },
        "results": results,
    }

# repair only active/past_due/unpaid subscriptions that are still missing metadata
@main.route("/admin/apply-missing-inspection-metadata", methods=["POST"])
def apply_missing_inspection_metadata():
    if not logged_in_or_dev():
        return redirect("/login")

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    mode = request.form.get("mode")

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config[
        "STRIPE_SECRET_KEY"
    ].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": (
                "You submitted mode=live, "
                "but Stripe key is not live."
            )
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": (
                "You submitted mode=test, "
                "but Stripe key is live."
            )
        }, 400

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    target_subscription_id = request.form.get(
        "subscription_id"
    )

    results = []
    log_rows = []

    checked_count = 0
    updated_count = 0
    skipped_complete_count = 0
    failed_count = 0

    os.makedirs("logs", exist_ok=True)

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H%M%S"
    )

    log_filename = (
        f"missing_inspection_metadata_apply_log_"
        f"{timestamp}.csv"
    )

    log_path = os.path.join(
        "logs",
        log_filename
    )

    for status in INSPECTION_BILLABLE_STATUSES:
        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100,
            expand=["data.customer"],
        )

        for subscription in subscriptions.auto_paging_iter():
            subscription_id = stripe_get(
                subscription,
                "id"
            )

            if (
                target_subscription_id
                and subscription_id != target_subscription_id
            ):
                continue

            checked_count += 1

            customer = stripe_get(
                subscription,
                "customer"
            )

            customer_id = stripe_get(
                customer,
                "id"
            )

            current_metadata = stripe_metadata_to_dict(
                stripe_get(
                    subscription,
                    "metadata",
                    {}
                ) or {}
            )

            missing_keys = (
                get_missing_inspection_metadata_keys(
                    current_metadata
                )
            )

            if not missing_keys:
                skipped_complete_count += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "skipped_already_complete",
                })

                continue

            contract_start_dt = (
                stripe_timestamp_to_utc_datetime(
                    stripe_get(customer, "created")
                )
            )

            if not contract_start_dt:
                failed_count += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "failed",
                    "reason": (
                        "missing_customer_created_date"
                    ),
                })

                log_rows.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "failed",
                    "missing_keys_before": ",".join(
                        missing_keys
                    ),
                    "reason": (
                        "missing_customer_created_date"
                    ),
                    "error": "",
                    "contract_start_date": "",
                    "contract_end_date": "",
                    "contract_term_years": "",
                    "inspection_fee_start_date": "",
                    "inspection_fee_end_date": "",
                    "inspection_fee_years": "",
                    "inspection_fee_status": "",
                    "billing_rule_version": "",
                })

                continue

            expected_inspection_end_dt = (
                contract_start_dt
                + relativedelta(years=3)
            )

            expected_contract_end_dt = (
                contract_start_dt
                + relativedelta(years=50)
            )

            metadata_to_merge = {
                "contract_start_date": (
                    contract_start_dt
                    .date()
                    .isoformat()
                ),
                "contract_end_date": (
                    expected_contract_end_dt
                    .date()
                    .isoformat()
                ),
                "contract_term_years": "50",
                "inspection_fee_start_date": (
                    contract_start_dt
                    .date()
                    .isoformat()
                ),
                "inspection_fee_end_date": (
                    expected_inspection_end_dt
                    .date()
                    .isoformat()
                ),
                "inspection_fee_years": "3",
                "inspection_fee_status": "active",
                "billing_rule_version": "1",
            }

            new_metadata = {
                **current_metadata,
                **metadata_to_merge,
            }

            try:
                stripe.Subscription.modify(
                    subscription_id,
                    metadata=new_metadata,
                )

                updated_count += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "metadata_updated",
                    "missing_keys_before": missing_keys,
                    "metadata": new_metadata,
                })

                log_rows.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "metadata_updated",
                    "missing_keys_before": ",".join(
                        missing_keys
                    ),
                    "reason": "",
                    "error": "",
                    "contract_start_date": (
                        new_metadata[
                            "contract_start_date"
                        ]
                    ),
                    "contract_end_date": (
                        new_metadata[
                            "contract_end_date"
                        ]
                    ),
                    "contract_term_years": (
                        new_metadata[
                            "contract_term_years"
                        ]
                    ),
                    "inspection_fee_start_date": (
                        new_metadata[
                            "inspection_fee_start_date"
                        ]
                    ),
                    "inspection_fee_end_date": (
                        new_metadata[
                            "inspection_fee_end_date"
                        ]
                    ),
                    "inspection_fee_years": (
                        new_metadata[
                            "inspection_fee_years"
                        ]
                    ),
                    "inspection_fee_status": (
                        new_metadata[
                            "inspection_fee_status"
                        ]
                    ),
                    "billing_rule_version": (
                        new_metadata[
                            "billing_rule_version"
                        ]
                    ),
                })

            except Exception as e:
                failed_count += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "failed",
                    "missing_keys_before": missing_keys,
                    "error": str(e),
                })

                log_rows.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "subscription_status": stripe_get(
                        subscription,
                        "status"
                    ),
                    "action": "failed",
                    "missing_keys_before": ",".join(
                        missing_keys
                    ),
                    "reason": "",
                    "error": str(e),
                    "contract_start_date": (
                        metadata_to_merge[
                            "contract_start_date"
                        ]
                    ),
                    "contract_end_date": (
                        metadata_to_merge[
                            "contract_end_date"
                        ]
                    ),
                    "contract_term_years": (
                        metadata_to_merge[
                            "contract_term_years"
                        ]
                    ),
                    "inspection_fee_start_date": (
                        metadata_to_merge[
                            "inspection_fee_start_date"
                        ]
                    ),
                    "inspection_fee_end_date": (
                        metadata_to_merge[
                            "inspection_fee_end_date"
                        ]
                    ),
                    "inspection_fee_years": (
                        metadata_to_merge[
                            "inspection_fee_years"
                        ]
                    ),
                    "inspection_fee_status": (
                        metadata_to_merge[
                            "inspection_fee_status"
                        ]
                    ),
                    "billing_rule_version": (
                        metadata_to_merge[
                            "billing_rule_version"
                        ]
                    ),
                })

                continue

    fieldnames = [
        "subscription_id",
        "customer_id",
        "subscription_status",
        "action",
        "missing_keys_before",
        "reason",
        "error",
        "contract_start_date",
        "contract_end_date",
        "contract_term_years",
        "inspection_fee_start_date",
        "inspection_fee_end_date",
        "inspection_fee_years",
        "inspection_fee_status",
        "billing_rule_version",
    ]

    with open(
        log_path,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(log_rows)

    return {
        "status": "ok",
        "mode": mode,
        "target_subscription_id": (
            target_subscription_id
        ),
        "statuses_checked": (
            INSPECTION_BILLABLE_STATUSES
        ),
        "checked_count": checked_count,
        "updated_count": updated_count,
        "skipped_complete_count": (
            skipped_complete_count
        ),
        "failed_count": failed_count,
        "results": results,
        "log_file": log_path,
    }

# product audit
@main.route("/admin/audit-products")
def audit_products(): 

    products= stripe.Product.list(
        active=True,
        limit=100,
    )

    results= []

    for product in products.auto_paging_iter():
        product_id= stripe_get(product, "id")
        name= stripe_get(product, "name", "")
        metadata= stripe_get(product, "metadata", {})

        if "inspect" in name.lower() or "inspection" in name.lower():
            results.append({
                "product_id": product_id,
                "name": name, 
                "metadata": metadata,
            })

    return {
        "count": len(results),
        "results": results,
    }

# audit_inspection_fee_items
@main.route("/admin/audit-inspection-fee-items", methods=["GET"])
def audit_inspection_fee_items():
    """
    Read-only audit route.

    Inspects subscription items for active, past_due, and unpaid
    subscriptions.

    The route collects:
    - Subscription information
    - Customer information
    - Subscription item information
    - Price information
    - Product information
    - Existing item and Product metadata

    It also summarizes:
    - All Products found
    - How many subscription items use each Product
    - How many subscriptions contain 1, 2, 3, etc. items
    - Whether customers whose description starts with "3" have
      inspection-fee items

    This route does not modify Stripe.
    """

    if not logged_in_or_dev():
        return redirect("/login")

    subscription_statuses = [
        "active",
        "past_due",
        "unpaid",
    ]

    results = []
    errors = []

    checked_subscription_count = 0
    checked_item_count = 0

    # Cache Stripe Products during this request.
    # Many subscription items reuse the same Product.
    product_cache = {}

    # Cache Stripe Customers during this request.
    # Normally each subscription has one customer, but caching still
    # prevents duplicate retrieval if a customer has multiple subscriptions.
    customer_cache = {}

    # One summary entry per unique Product ID.
    product_summary = {}

    # Records the number of items inside each subscription.
    #
    # Example:
    # {
    #     "sub_123": 2,
    #     "sub_456": 1,
    # }
    subscription_item_counts = {}

    # Stores information needed to inspect subscriptions after all
    # of their items have been processed.
    subscription_summary = {}

    for requested_status in subscription_statuses:

        subscriptions = stripe.Subscription.list(
            status=requested_status,
            limit=100,
        )

        for subscription in subscriptions.auto_paging_iter():

            subscription_id = stripe_get(
                subscription,
                "id",
            )

            customer_id = stripe_get(
                subscription,
                "customer",
            )

            subscription_status = stripe_get(
                subscription,
                "status",
            )

            checked_subscription_count += 1

            # Start this subscription with zero counted items.
            subscription_item_counts[subscription_id] = 0

            # ---------------------------------------------------------
            # Retrieve customer information
            # ---------------------------------------------------------

            customer = {}
            customer_name = None
            customer_email = None
            customer_description = None

            if isinstance(customer_id, str):

                if customer_id not in customer_cache:

                    try:
                        customer_cache[customer_id] = (
                            stripe.Customer.retrieve(customer_id)
                        )

                    except Exception as customer_error:

                        customer_cache[customer_id] = None

                        errors.append({
                            "subscription_id": subscription_id,
                            "subscription_item_id": None,
                            "customer_id": customer_id,
                            "product_id": None,
                            "error_type": (
                                "customer_retrieval_failed"
                            ),
                            "error": str(customer_error),
                        })

                customer = (
                    customer_cache.get(customer_id)
                    or {}
                )

            elif customer_id:
                # Stripe may return an expanded Customer object.
                customer = customer_id

                customer_id = stripe_get(
                    customer,
                    "id",
                )

            if customer:

                customer_name = stripe_get(
                    customer,
                    "name",
                )

                customer_email = stripe_get(
                    customer,
                    "email",
                )

                customer_description = stripe_get(
                    customer,
                    "description",
                )

            normalized_customer_description = (
                str(customer_description).strip()
                if customer_description is not None
                else ""
            )

            customer_description_starts_with_3 = (
                normalized_customer_description.startswith("3")
            )

            subscription_summary[subscription_id] = {
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "customer_email": customer_email,
                "customer_description": customer_description,
                "customer_description_starts_with_3": (
                    customer_description_starts_with_3
                ),
                "subscription_status": subscription_status,
                "item_count": 0,
                "product_ids": [],
                "product_names": [],
                "inspection_candidate_item_count": 0,
            }

            try:
                # Listing subscription items separately ensures that
                # pagination is handled correctly.
                subscription_items = stripe.SubscriptionItem.list(
                    subscription=subscription_id,
                    limit=100,
                )

                for item in subscription_items.auto_paging_iter():

                    checked_item_count += 1

                    subscription_item_counts[subscription_id] += 1

                    subscription_summary[
                        subscription_id
                    ]["item_count"] += 1

                    subscription_item_id = stripe_get(
                        item,
                        "id",
                    )

                    quantity = stripe_get(
                        item,
                        "quantity",
                    )

                    item_metadata = stripe_metadata_to_dict(
                        stripe_get(
                            item,
                            "metadata",
                            {},
                        )
                    )

                    # -------------------------------------------------
                    # Price information
                    # -------------------------------------------------

                    price = stripe_get(
                        item,
                        "price",
                        {},
                    ) or {}

                    price_id = stripe_get(
                        price,
                        "id",
                    )

                    price_nickname = stripe_get(
                        price,
                        "nickname",
                    )

                    price_lookup_key = stripe_get(
                        price,
                        "lookup_key",
                    )

                    unit_amount = stripe_get(
                        price,
                        "unit_amount",
                    )

                    unit_amount_decimal = stripe_get(
                        price,
                        "unit_amount_decimal",
                    )

                    currency = stripe_get(
                        price,
                        "currency",
                    )

                    price_active = stripe_get(
                        price,
                        "active",
                    )

                    recurring = stripe_get(
                        price,
                        "recurring",
                        {},
                    ) or {}

                    recurring_interval = stripe_get(
                        recurring,
                        "interval",
                    )

                    recurring_interval_count = stripe_get(
                        recurring,
                        "interval_count",
                    )

                    recurring_usage_type = stripe_get(
                        recurring,
                        "usage_type",
                    )

                    # -------------------------------------------------
                    # Product information
                    # -------------------------------------------------

                    product_reference = stripe_get(
                        price,
                        "product",
                    )

                    product = {}
                    product_id = None
                    product_name = None
                    product_description = None
                    product_active = None
                    product_metadata = {}

                    if isinstance(product_reference, str):

                        product_id = product_reference

                        if product_id not in product_cache:

                            try:
                                product_cache[product_id] = (
                                    stripe.Product.retrieve(
                                        product_id
                                    )
                                )

                            except Exception as product_error:

                                product_cache[product_id] = None

                                errors.append({
                                    "subscription_id": (
                                        subscription_id
                                    ),
                                    "subscription_item_id": (
                                        subscription_item_id
                                    ),
                                    "customer_id": customer_id,
                                    "product_id": product_id,
                                    "error_type": (
                                        "product_retrieval_failed"
                                    ),
                                    "error": str(product_error),
                                })

                        product = (
                            product_cache.get(product_id)
                            or {}
                        )

                    elif product_reference:
                        # Stripe may return an expanded Product.
                        product = product_reference

                        product_id = stripe_get(
                            product,
                            "id",
                        )

                    if product:

                        product_name = stripe_get(
                            product,
                            "name",
                        )

                        product_description = stripe_get(
                            product,
                            "description",
                        )

                        product_active = stripe_get(
                            product,
                            "active",
                        )

                        product_metadata = (
                            stripe_metadata_to_dict(
                                stripe_get(
                                    product,
                                    "metadata",
                                    {},
                                )
                            )
                        )

                    subscription_summary[
                        subscription_id
                    ]["product_ids"].append(product_id)

                    subscription_summary[
                        subscription_id
                    ]["product_names"].append(product_name)

                    # -------------------------------------------------
                    # Discovery-only inspection candidate
                    # -------------------------------------------------
                    #
                    # This does NOT become the permanent production rule.
                    # It is only used to make the audit easier to review.
                    #
                    # The final metadata migration will use confirmed
                    # Product IDs.
                    # -------------------------------------------------

                    normalized_product_name = (
                        str(product_name).strip().lower()
                        if product_name
                        else ""
                    )

                    normalized_product_description = (
                        str(product_description).strip().lower()
                        if product_description
                        else ""
                    )

                    inspection_name_candidate = (
                        "inspect" in normalized_product_name
                        or
                        "inspect" in normalized_product_description
                    )

                    if inspection_name_candidate:
                        subscription_summary[
                            subscription_id
                        ]["inspection_candidate_item_count"] += 1

                    # -------------------------------------------------
                    # Build Product summary
                    # -------------------------------------------------

                    product_summary_key = (
                        product_id
                        if product_id
                        else "missing_product_id"
                    )

                    if product_summary_key not in product_summary:

                        product_summary[product_summary_key] = {
                            "product_id": product_id,
                            "product_name": product_name,
                            "product_description": (
                                product_description
                            ),
                            "product_active": product_active,
                            "product_metadata": product_metadata,
                            "item_count": 0,
                            "subscription_ids": set(),
                            "customer_ids": set(),
                            "price_ids": set(),
                            "unit_amounts": set(),
                            "currencies": set(),
                            "intervals": set(),
                            "interval_counts": set(),
                            "ottawa_item_count": 0,
                            "non_ottawa_item_count": 0,
                        }

                    summary = product_summary[
                        product_summary_key
                    ]

                    summary["item_count"] += 1

                    summary["subscription_ids"].add(
                        subscription_id
                    )

                    if customer_id:
                        summary["customer_ids"].add(
                            customer_id
                        )

                    if price_id:
                        summary["price_ids"].add(
                            price_id
                        )

                    if unit_amount is not None:
                        summary["unit_amounts"].add(
                            unit_amount
                        )

                    if currency:
                        summary["currencies"].add(
                            currency
                        )

                    if recurring_interval:
                        summary["intervals"].add(
                            recurring_interval
                        )

                    if recurring_interval_count is not None:
                        summary["interval_counts"].add(
                            recurring_interval_count
                        )

                    if customer_description_starts_with_3:
                        summary["ottawa_item_count"] += 1
                    else:
                        summary["non_ottawa_item_count"] += 1

                    # -------------------------------------------------
                    # Store full item-level audit row
                    # -------------------------------------------------

                    results.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "customer_email": customer_email,
                        "customer_description": (
                            customer_description
                        ),
                        "customer_description_starts_with_3": (
                            customer_description_starts_with_3
                        ),
                        "subscription_status": (
                            subscription_status
                        ),
                        "subscription_item_id": (
                            subscription_item_id
                        ),
                        "quantity": quantity,
                        "product_id": product_id,
                        "product_name": product_name,
                        "product_description": (
                            product_description
                        ),
                        "product_active": product_active,
                        "price_id": price_id,
                        "price_nickname": price_nickname,
                        "price_lookup_key": price_lookup_key,
                        "price_active": price_active,
                        "unit_amount": unit_amount,
                        "unit_amount_decimal": (
                            unit_amount_decimal
                        ),
                        "currency": currency,
                        "recurring_interval": (
                            recurring_interval
                        ),
                        "recurring_interval_count": (
                            recurring_interval_count
                        ),
                        "recurring_usage_type": (
                            recurring_usage_type
                        ),
                        "item_metadata": item_metadata,
                        "product_metadata": product_metadata,
                        "inspection_name_candidate": (
                            inspection_name_candidate
                        ),
                    })

            except Exception as subscription_error:

                errors.append({
                    "subscription_id": subscription_id,
                    "subscription_item_id": None,
                    "customer_id": customer_id,
                    "product_id": None,
                    "error_type": (
                        "subscription_item_audit_failed"
                    ),
                    "error": str(subscription_error),
                })

    # -------------------------------------------------------------
    # Summarize subscription item counts
    # -------------------------------------------------------------

    subscription_item_count_summary = {}

    for item_count in subscription_item_counts.values():

        item_count_key = str(item_count)

        subscription_item_count_summary[item_count_key] = (
            subscription_item_count_summary.get(
                item_count_key,
                0,
            )
            + 1
        )

    # -------------------------------------------------------------
    # Create JSON-safe Product summary
    # -------------------------------------------------------------

    product_summary_results = []

    for summary in product_summary.values():

        product_summary_results.append({
            "product_id": summary["product_id"],
            "product_name": summary["product_name"],
            "product_description": (
                summary["product_description"]
            ),
            "product_active": summary["product_active"],
            "product_metadata": summary["product_metadata"],
            "item_count": summary["item_count"],
            "subscription_count": len(
                summary["subscription_ids"]
            ),
            "customer_count": len(
                summary["customer_ids"]
            ),
            "price_ids": sorted(
                summary["price_ids"]
            ),
            "unit_amounts": sorted(
                summary["unit_amounts"]
            ),
            "currencies": sorted(
                summary["currencies"]
            ),
            "intervals": sorted(
                summary["intervals"]
            ),
            "interval_counts": sorted(
                summary["interval_counts"]
            ),
            "ottawa_item_count": (
                summary["ottawa_item_count"]
            ),
            "non_ottawa_item_count": (
                summary["non_ottawa_item_count"]
            ),
        })

    product_summary_results.sort(
        key=lambda row: row["item_count"],
        reverse=True,
    )

    # -------------------------------------------------------------
    # Summarize subscriptions by Ottawa/non-Ottawa and item count
    # -------------------------------------------------------------

    subscription_summary_results = list(
        subscription_summary.values()
    )

    subscription_summary_results.sort(
        key=lambda row: (
            row["item_count"],
            str(row["customer_description"] or ""),
        )
    )

    ottawa_subscription_count = 0
    non_ottawa_subscription_count = 0

    ottawa_one_item_count = 0
    ottawa_two_item_count = 0
    ottawa_other_item_count = 0

    non_ottawa_one_item_count = 0
    non_ottawa_two_item_count = 0
    non_ottawa_other_item_count = 0

    ottawa_with_inspection_candidate_count = 0
    non_ottawa_without_inspection_candidate_count = 0

    for summary in subscription_summary_results:

        is_ottawa = summary[
            "customer_description_starts_with_3"
        ]

        item_count = summary["item_count"]

        inspection_candidate_count = summary[
            "inspection_candidate_item_count"
        ]

        if is_ottawa:
            ottawa_subscription_count += 1

            if item_count == 1:
                ottawa_one_item_count += 1
            elif item_count == 2:
                ottawa_two_item_count += 1
            else:
                ottawa_other_item_count += 1

            if inspection_candidate_count > 0:
                ottawa_with_inspection_candidate_count += 1

        else:
            non_ottawa_subscription_count += 1

            if item_count == 1:
                non_ottawa_one_item_count += 1
            elif item_count == 2:
                non_ottawa_two_item_count += 1
            else:
                non_ottawa_other_item_count += 1

            if inspection_candidate_count == 0:
                non_ottawa_without_inspection_candidate_count += 1

    regional_summary = {
        "ottawa_rule_used_for_audit": (
            "customer description starts with 3"
        ),
        "ottawa_subscription_count": (
            ottawa_subscription_count
        ),
        "non_ottawa_subscription_count": (
            non_ottawa_subscription_count
        ),
        "ottawa_one_item_count": (
            ottawa_one_item_count
        ),
        "ottawa_two_item_count": (
            ottawa_two_item_count
        ),
        "ottawa_other_item_count": (
            ottawa_other_item_count
        ),
        "non_ottawa_one_item_count": (
            non_ottawa_one_item_count
        ),
        "non_ottawa_two_item_count": (
            non_ottawa_two_item_count
        ),
        "non_ottawa_other_item_count": (
            non_ottawa_other_item_count
        ),
        "ottawa_with_inspection_candidate_count": (
            ottawa_with_inspection_candidate_count
        ),
        "non_ottawa_without_inspection_candidate_count": (
            non_ottawa_without_inspection_candidate_count
        ),
    }

    # -------------------------------------------------------------
    # Create CSV files
    # -------------------------------------------------------------

    os.makedirs(
        "logs",
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d_%H%M%S")

    item_csv_path = os.path.join(
        "logs",
        f"inspection_fee_item_audit_{timestamp}.csv",
    )

    product_csv_path = os.path.join(
        "logs",
        f"inspection_fee_product_summary_{timestamp}.csv",
    )

    subscription_csv_path = os.path.join(
        "logs",
        f"inspection_fee_subscription_summary_{timestamp}.csv",
    )

    # -------------------------------------------------------------
    # Item-level CSV
    # -------------------------------------------------------------

    item_fieldnames = [
        "subscription_id",
        "customer_id",
        "customer_name",
        "customer_email",
        "customer_description",
        "customer_description_starts_with_3",
        "subscription_status",
        "subscription_item_id",
        "quantity",
        "product_id",
        "product_name",
        "product_description",
        "product_active",
        "price_id",
        "price_nickname",
        "price_lookup_key",
        "price_active",
        "unit_amount",
        "unit_amount_decimal",
        "currency",
        "recurring_interval",
        "recurring_interval_count",
        "recurring_usage_type",
        "item_metadata",
        "product_metadata",
        "inspection_name_candidate",
    ]

    with open(
        item_csv_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as item_csv_file:

        writer = csv.DictWriter(
            item_csv_file,
            fieldnames=item_fieldnames,
        )

        writer.writeheader()

        for result in results:

            csv_row = result.copy()

            csv_row["item_metadata"] = json.dumps(
                result["item_metadata"],
                sort_keys=True,
            )

            csv_row["product_metadata"] = json.dumps(
                result["product_metadata"],
                sort_keys=True,
            )

            writer.writerow(csv_row)

    # -------------------------------------------------------------
    # Product-summary CSV
    # -------------------------------------------------------------

    product_fieldnames = [
        "product_id",
        "product_name",
        "product_description",
        "product_active",
        "product_metadata",
        "item_count",
        "subscription_count",
        "customer_count",
        "price_ids",
        "unit_amounts",
        "currencies",
        "intervals",
        "interval_counts",
        "ottawa_item_count",
        "non_ottawa_item_count",
    ]

    with open(
        product_csv_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as product_csv_file:

        writer = csv.DictWriter(
            product_csv_file,
            fieldnames=product_fieldnames,
        )

        writer.writeheader()

        for result in product_summary_results:

            csv_row = result.copy()

            csv_row["product_metadata"] = json.dumps(
                result["product_metadata"],
                sort_keys=True,
            )

            csv_row["price_ids"] = json.dumps(
                result["price_ids"],
            )

            csv_row["unit_amounts"] = json.dumps(
                result["unit_amounts"],
            )

            csv_row["currencies"] = json.dumps(
                result["currencies"],
            )

            csv_row["intervals"] = json.dumps(
                result["intervals"],
            )

            csv_row["interval_counts"] = json.dumps(
                result["interval_counts"],
            )

            writer.writerow(csv_row)

    # -------------------------------------------------------------
    # Subscription-summary CSV
    # -------------------------------------------------------------

    subscription_fieldnames = [
        "subscription_id",
        "customer_id",
        "customer_name",
        "customer_email",
        "customer_description",
        "customer_description_starts_with_3",
        "subscription_status",
        "item_count",
        "product_ids",
        "product_names",
        "inspection_candidate_item_count",
    ]

    with open(
        subscription_csv_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as subscription_csv_file:

        writer = csv.DictWriter(
            subscription_csv_file,
            fieldnames=subscription_fieldnames,
        )

        writer.writeheader()

        for result in subscription_summary_results:

            csv_row = result.copy()

            csv_row["product_ids"] = json.dumps(
                result["product_ids"],
            )

            csv_row["product_names"] = json.dumps(
                result["product_names"],
            )

            writer.writerow(csv_row)

    return {
        "status": "audit_complete",
        "read_only": True,
        "checked_subscription_count": (
            checked_subscription_count
        ),
        "checked_item_count": checked_item_count,
        "unique_product_count": len(
            product_summary_results
        ),
        "unique_customer_count": len(
            customer_cache
        ),
        "subscription_item_count_summary": (
            subscription_item_count_summary
        ),
        "regional_summary": regional_summary,
        "product_summary": product_summary_results,
        "error_count": len(errors),
        "errors": errors[:50],
        "item_log_file": item_csv_path,
        "product_summary_log_file": product_csv_path,
        "subscription_summary_log_file": (
            subscription_csv_path
        ),
        "sample_results": results[:20],
    }

# configuration constants
INSPECTION_FEE_PRODUCT_IDS = {
    "prod_S2qigSJLyqaK77",
    "prod_QRb4nqXhaz7pMn",
}

SUBSCRIPTION_ITEM_TYPE_METADATA_KEY = "item_type"

INSPECTION_FEE_ITEM_TYPE = "inspection_fee"
MONTHLY_SERVICE_ITEM_TYPE = "monthly_service_fee"

# Item-type detection helper
def determine_subscription_item_type(
    product_id,
    product_metadata,
):
    """
    Determine the intended subscription-item metadata type.

    Rules:
    - Two manually confirmed Product IDs are inspection fees.
    - Products with increaseable=true are monthly service fees.
    - Anything else is unknown and must not be updated.

    Returns:
        "inspection_fee"
        "monthly_service_fee"
        None
    """

    product_metadata = product_metadata or {}

    if product_id in INSPECTION_FEE_PRODUCT_IDS:
        return INSPECTION_FEE_ITEM_TYPE

    increaseable_value = str(
        product_metadata.get(
            "increaseable",
            "",
        )
    ).strip().lower()

    if increaseable_value == "true":
        return MONTHLY_SERVICE_ITEM_TYPE

    return None

# Shared subscription-item collection helper
# Both preview and apply need to inspect exactly the same subscriptions and use exactly the same classification rules
def collect_subscription_item_metadata_candidates():
    """
    Collect and classify subscription items for metadata migration.

    This helper reads Stripe only. It does not update anything.

    Returns:
        {
            "results": [...],
            "errors": [...],
            "checked_subscription_count": int,
            "checked_item_count": int,
        }
    """

    subscription_statuses = [
        "active",
        "past_due",
        "unpaid",
    ]

    results = []
    errors = []

    checked_subscription_count = 0
    checked_item_count = 0

    # Avoid retrieving the same Product repeatedly.
    product_cache = {}

    for requested_status in subscription_statuses:

        try:
            subscriptions = stripe.Subscription.list(
                status=requested_status,
                limit=100,
            )

            for subscription in subscriptions.auto_paging_iter():

                subscription_id = stripe_get(
                    subscription,
                    "id",
                )

                customer_id = stripe_get(
                    subscription,
                    "customer",
                )

                subscription_status = stripe_get(
                    subscription,
                    "status",
                )

                checked_subscription_count += 1

                try:
                    subscription_items = (
                        stripe.SubscriptionItem.list(
                            subscription=subscription_id,
                            limit=100,
                        )
                    )

                    for item in (
                        subscription_items.auto_paging_iter()
                    ):

                        checked_item_count += 1

                        subscription_item_id = stripe_get(
                            item,
                            "id",
                        )

                        current_metadata = (
                            stripe_metadata_to_dict(
                                stripe_get(
                                    item,
                                    "metadata",
                                    {},
                                )
                            )
                        )

                        current_item_type = (
                            current_metadata.get(
                                SUBSCRIPTION_ITEM_TYPE_METADATA_KEY
                            )
                        )

                        price = stripe_get(
                            item,
                            "price",
                            {},
                        ) or {}

                        price_id = stripe_get(
                            price,
                            "id",
                        )

                        unit_amount = stripe_get(
                            price,
                            "unit_amount",
                        )

                        currency = stripe_get(
                            price,
                            "currency",
                        )

                        product_reference = stripe_get(
                            price,
                            "product",
                        )

                        product = {}
                        product_id = None
                        product_name = None
                        product_metadata = {}

                        if isinstance(
                            product_reference,
                            str,
                        ):
                            product_id = product_reference

                            if product_id not in product_cache:

                                try:
                                    product_cache[product_id] = (
                                        stripe.Product.retrieve(
                                            product_id
                                        )
                                    )

                                except Exception as product_error:
                                    product_cache[product_id] = None

                                    errors.append({
                                        "subscription_id": (
                                            subscription_id
                                        ),
                                        "subscription_item_id": (
                                            subscription_item_id
                                        ),
                                        "customer_id": customer_id,
                                        "product_id": product_id,
                                        "error_type": (
                                            "product_retrieval_failed"
                                        ),
                                        "error": str(product_error),
                                    })

                            product = (
                                product_cache.get(product_id)
                                or {}
                            )

                        elif product_reference:
                            product = product_reference

                            product_id = stripe_get(
                                product,
                                "id",
                            )

                        if product:
                            product_name = stripe_get(
                                product,
                                "name",
                            )

                            product_metadata = (
                                stripe_metadata_to_dict(
                                    stripe_get(
                                        product,
                                        "metadata",
                                        {},
                                    )
                                )
                            )

                        intended_item_type = (
                            determine_subscription_item_type(
                                product_id=product_id,
                                product_metadata=product_metadata,
                            )
                        )

                        # -----------------------------------------
                        # Determine the migration action
                        # -----------------------------------------

                        if intended_item_type is None:
                            action = "unknown_product"
                            reason = (
                                "Product is not a confirmed inspection "
                                "Product and does not have "
                                "increaseable=true."
                            )

                        elif current_item_type is None:
                            action = "would_update"
                            reason = (
                                "item_type metadata is missing."
                            )

                        elif (
                            current_item_type
                            == intended_item_type
                        ):
                            action = "already_complete"
                            reason = (
                                "Existing item_type metadata is correct."
                            )

                        else:
                            action = "conflicting_metadata"
                            reason = (
                                "Existing item_type does not match the "
                                "type determined from confirmed Product "
                                "rules."
                            )

                        merged_metadata = current_metadata.copy()

                        if intended_item_type is not None:
                            merged_metadata[
                                SUBSCRIPTION_ITEM_TYPE_METADATA_KEY
                            ] = intended_item_type

                        results.append({
                            "action": action,
                            "reason": reason,
                            "subscription_id": subscription_id,
                            "customer_id": customer_id,
                            "subscription_status": (
                                subscription_status
                            ),
                            "subscription_item_id": (
                                subscription_item_id
                            ),
                            "product_id": product_id,
                            "product_name": product_name,
                            "price_id": price_id,
                            "unit_amount": unit_amount,
                            "currency": currency,
                            "current_item_type": (
                                current_item_type
                            ),
                            "intended_item_type": (
                                intended_item_type
                            ),
                            "current_metadata": (
                                current_metadata
                            ),
                            "merged_metadata": (
                                merged_metadata
                            ),
                            "product_metadata": (
                                product_metadata
                            ),
                        })

                except Exception as subscription_error:
                    errors.append({
                        "subscription_id": subscription_id,
                        "subscription_item_id": None,
                        "customer_id": customer_id,
                        "product_id": None,
                        "error_type": (
                            "subscription_item_collection_failed"
                        ),
                        "error": str(subscription_error),
                    })

        except Exception as status_error:
            errors.append({
                "subscription_id": None,
                "subscription_item_id": None,
                "customer_id": None,
                "product_id": None,
                "error_type": (
                    "subscription_status_list_failed"
                ),
                "requested_status": requested_status,
                "error": str(status_error),
            })

    return {
        "results": results,
        "errors": errors,
        "checked_subscription_count": (
            checked_subscription_count
        ),
        "checked_item_count": checked_item_count,
    }

# CSV-writing helper
def write_subscription_item_metadata_csv(results, filename_prefix):
    """
    Write subscription-item metadata results to a CSV file.

    Returns the generated CSV path.
    """

    os.makedirs(
        "logs",
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d_%H%M%S")

    csv_path = os.path.join(
        "logs",
        f"{filename_prefix}_{timestamp}.csv",
    )

    fieldnames = [
        "action",
        "reason",
        "subscription_id",
        "customer_id",
        "subscription_status",
        "subscription_item_id",
        "product_id",
        "product_name",
        "price_id",
        "unit_amount",
        "currency",
        "current_item_type",
        "intended_item_type",
        "current_metadata",
        "merged_metadata",
        "product_metadata",
        "apply_status",
        "apply_error",
    ]

    with open(
        csv_path,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for result in results:
            csv_row = result.copy()

            csv_row["current_metadata"] = json.dumps(
                result.get(
                    "current_metadata",
                    {},
                ),
                sort_keys=True,
            )

            csv_row["merged_metadata"] = json.dumps(
                result.get(
                    "merged_metadata",
                    {},
                ),
                sort_keys=True,
            )

            csv_row["product_metadata"] = json.dumps(
                result.get(
                    "product_metadata",
                    {},
                ),
                sort_keys=True,
            )

            csv_row.setdefault(
                "apply_status",
                "",
            )

            csv_row.setdefault(
                "apply_error",
                "",
            )

            writer.writerow(csv_row)

    return csv_path

# 
def build_phase_business_signature(phase):
    items = []

    for item in stripe_get(phase, "items", []):
        price = stripe_get(item, "price", {})
        price_id = (
            stripe_get(price, "id")
            if not isinstance(price, str)
            else price
        )

        item_signature = {
            "price_id": price_id,
            "quantity": stripe_get(item, "quantity", 1),
            "tax_rates": sorted([
                stripe_get(tax_rate, "id")
                if not isinstance(tax_rate, str)
                else tax_rate
                for tax_rate in stripe_get(item, "tax_rates", [])
            ]),
        }

        items.append(item_signature)

    items.sort(
        key=lambda item: (
            item["price_id"] or "",
            item["quantity"],
            tuple(item["tax_rates"]),
        )
    )

    return {
        "items": items,
        "add_invoice_items": stripe_value_to_plain_python(stripe_get(phase, "add_invoice_items", []) or []),
        "discounts": stripe_value_to_plain_python(stripe_get(phase, "discounts", []) or []),
        "default_tax_rates": sorted([
            stripe_get(tax_rate, "id")
            if not isinstance(tax_rate, str)
            else tax_rate
            for tax_rate in stripe_get(
                phase,
                "default_tax_rates",
                [],
            )
        ]),
        "automatic_tax": stripe_value_to_plain_python(stripe_get(phase, "automatic_tax")),
        "collection_method": stripe_get(
            phase,
            "collection_method",
        ),
        "default_payment_method": stripe_get(
            phase,
            "default_payment_method",
        ),
        "invoice_settings": stripe_value_to_plain_python(stripe_get(phase, "invoice_settings")),
        "billing_cycle_anchor": stripe_get(
            phase,
            "billing_cycle_anchor",
        ),
        "billing_thresholds": stripe_value_to_plain_python(stripe_get(phase, "billing_thresholds")),
        "trial_end": stripe_get(
            phase,
            "trial_end",
        ),
        "metadata": stripe_metadata_to_dict(stripe_get(phase, "metadata", {}) or {}),
    }

# {} in the printed output does not guarantee the object is a Python dict.
# Many libraries (Stripe, SQLAlchemy, Pydantic, etc.) create custom objects that behave like dictionaries and print like dictionaries, but are actually custom classes underneath.
# json.dumps() only knows how to serialize standard Python types unless you convert those custom objects first.
# Serialize = convert an object into a format that can be stored or sent somewhere else.
# Think of it as packing an object into a standard format.
def stripe_value_to_plain_python(value):
    if value is None:
        return None

    if hasattr(value, "to_dict_recursive"):
        return stripe_value_to_plain_python(
            value.to_dict_recursive()
        )

    if isinstance(value, dict):
        return {
            str(key): stripe_value_to_plain_python(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            stripe_value_to_plain_python(item)
            for item in value
        ]

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)

# signature_differences
def get_signature_differences(
    first_signature,
    other_signature,
):
    differences = []

    all_keys = sorted(
        set(first_signature.keys())
        | set(other_signature.keys())
    )

    for key in all_keys:
        if first_signature.get(key) != other_signature.get(key):
            differences.append(key)

    return differences

# 
def apply_contract_end_migration_internal(
    subscription_id,
    requested_action,
    mode,
    write_individual_log=True,
):
    """
    Perform one contract-end migration.

    This is a normal Python helper, not a Flask route.

    Supported actions:
    - release_schedule
    - clear_cancel_at

    Returns:
        response_body, http_status
    """

    migrated_at = datetime.now(timezone.utc).isoformat()

    log_row = {
        "migrated_at": migrated_at,
        "mode": mode,
        "action": requested_action,
        "subscription_id": subscription_id,
        "customer_id": "",
        "schedule_id_before": "",
        "status_before": "",
        "status_after": "",
        "cancel_at_before": "",
        "cancel_at_after": "",
        "cancel_at_period_end_before": "",
        "cancel_at_period_end_after": "",
        "billing_cycle_anchor_before": "",
        "billing_cycle_anchor_after": "",
        "items_before": [],
        "items_after": [],
        "schedule_cleared": False,
        "cancel_at_cleared": False,
        "cancel_at_period_end_false": False,
        "billing_cycle_anchor_unchanged": False,
        "items_unchanged": False,
        "subscription_still_billable": False,
        "verification_passed": False,
        "result_status": "started",
        "error": "",
    }

    def save_individual_log():
        if not write_individual_log:
            return ""

        return write_contract_end_migration_log(log_row)

    try:
        subscription_before = stripe.Subscription.retrieve(subscription_id)

        customer_reference = stripe_get(subscription_before, "customer")

        if isinstance(customer_reference, str):
            customer_id = customer_reference
        else:
            customer_id = stripe_get(customer_reference, "id")

        status_before = stripe_get(subscription_before, "status")
        schedule_before = stripe_get(subscription_before, "schedule")
        cancel_at_before = stripe_get(subscription_before, "cancel_at")
        cancel_at_period_end_before = stripe_get(
            subscription_before,
            "cancel_at_period_end",
            False,
        )
        billing_cycle_anchor_before = stripe_get(subscription_before, "billing_cycle_anchor")
        items_before = get_contract_end_migration_item_snapshot(subscription_before)

        if isinstance(schedule_before, str):
            schedule_id_before = schedule_before
        else:
            schedule_id_before = stripe_get(schedule_before, "id")

        log_row.update({
            "customer_id": customer_id or "",
            "schedule_id_before": schedule_id_before or "",
            "status_before": status_before or "",
            "cancel_at_before": cancel_at_before or "",
            "cancel_at_period_end_before": cancel_at_period_end_before,
            "billing_cycle_anchor_before": billing_cycle_anchor_before or "",
            "items_before": items_before,
        })

        if requested_action == "release_schedule":
            if not schedule_id_before:
                log_row["result_status"] = "rejected"
                log_row["error"] = (
                    "Subscription does not have an attached schedule."
                )

                csv_path = save_individual_log()

                return {
                    "status": "rejected",
                    "subscription_id": subscription_id,
                    "action": requested_action,
                    "error": log_row["error"],
                    "log_file": csv_path,
                }, 400

            schedule = stripe.SubscriptionSchedule.retrieve(schedule_id_before)

            schedule_status = stripe_get(schedule, "status")
            schedule_subscription = stripe_get(schedule, "subscription")

            if isinstance(schedule_subscription, str):
                schedule_subscription_id = schedule_subscription
            else:
                schedule_subscription_id = stripe_get(schedule_subscription,"id")

            if schedule_subscription_id != subscription_id:
                log_row["result_status"] = "rejected"
                log_row["error"] = (
                    "The attached schedule does not belong to "
                    "the requested subscription."
                )

                csv_path = save_individual_log()

                return {
                    "status": "rejected",
                    "subscription_id": subscription_id,
                    "schedule_id": schedule_id_before,
                    "error": log_row["error"],
                    "log_file": csv_path,
                }, 400

            if schedule_status not in ["active", "not_started"]:
                log_row["result_status"] = "rejected"
                log_row["error"] = (
                    "Schedule cannot be released because its status is "
                    f"{schedule_status}."
                )

                csv_path = save_individual_log()

                return {
                    "status": "rejected",
                    "subscription_id": subscription_id,
                    "schedule_id": schedule_id_before,
                    "error": log_row["error"],
                    "log_file": csv_path,
                }, 400

            stripe.SubscriptionSchedule.release(schedule_id_before)

            # Releasing the schedule removes the schedule association,
            # but Stripe may leave the old cancel_at date on the subscription.
            # Clear it so the subscription becomes truly open-ended.
            stripe.Subscription.modify(
                subscription_id,
                cancel_at="",
                proration_behavior="none",
            )

        elif requested_action == "clear_cancel_at":
            if schedule_id_before:
                log_row["result_status"] = "rejected"
                log_row["error"] = (
                    "Subscription has a schedule. Use "
                    "action=release_schedule instead."
                )

                csv_path = save_individual_log()

                return {
                    "status": "rejected",
                    "subscription_id": subscription_id,
                    "schedule_id": schedule_id_before,
                    "error": log_row["error"],
                    "log_file": csv_path,
                }, 400

            if cancel_at_before is None:
                log_row["result_status"] = "rejected"
                log_row["error"] = (
                    "Subscription does not have cancel_at set."
                )

                csv_path = save_individual_log()

                return {
                    "status": "rejected",
                    "subscription_id": subscription_id,
                    "error": log_row["error"],
                    "log_file": csv_path,
                }, 400

            stripe.Subscription.modify(
                subscription_id,
                cancel_at="",
                proration_behavior="none",
            )

        subscription_after = stripe.Subscription.retrieve(subscription_id)

        status_after = stripe_get(subscription_after, "status")
        schedule_after = stripe_get(subscription_after, "schedule")
        cancel_at_after = stripe_get(subscription_after, "cancel_at")
        cancel_at_period_end_after = stripe_get(subscription_after, "cancel_at_period_end", False)
        billing_cycle_anchor_after = stripe_get(subscription_after, "billing_cycle_anchor")
        items_after = get_contract_end_migration_item_snapshot(subscription_after)

        schedule_cleared = schedule_after is None
        cancel_at_cleared = cancel_at_after is None
        cancel_at_period_end_false = cancel_at_period_end_after is False

        billing_cycle_anchor_unchanged = (
            billing_cycle_anchor_before == billing_cycle_anchor_after
        )

        items_unchanged = items_before == items_after

        subscription_still_billable = (
            status_after in INSPECTION_BILLABLE_STATUSES
        )

        verification_passed = (
            schedule_cleared
            and cancel_at_cleared
            and cancel_at_period_end_false
            and billing_cycle_anchor_unchanged
            and items_unchanged
            and subscription_still_billable
        )

        log_row.update({
            "status_after": status_after or "",
            "cancel_at_after": cancel_at_after or "",
            "cancel_at_period_end_after": cancel_at_period_end_after,
            "billing_cycle_anchor_after": billing_cycle_anchor_after or "",
            "items_after": items_after,
            "schedule_cleared": schedule_cleared,
            "cancel_at_cleared": cancel_at_cleared,
            "cancel_at_period_end_false": cancel_at_period_end_false,
            "billing_cycle_anchor_unchanged": (
                billing_cycle_anchor_unchanged
            ),
            "items_unchanged": items_unchanged,
            "subscription_still_billable": (
                subscription_still_billable
            ),
            "verification_passed": verification_passed,
            "result_status": (
                "verified_success"
                if verification_passed
                else "verification_failed"
            ),
        })

        csv_path = save_individual_log()

        response_status = 200 if verification_passed else 500

        return {
            "status": log_row["result_status"],
            "mode": mode,
            "action": requested_action,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "verification_passed": verification_passed,
            "before": {
                "status": status_before,
                "schedule": schedule_id_before,
                "cancel_at": cancel_at_before,
                "cancel_at_period_end": cancel_at_period_end_before,
                "billing_cycle_anchor": billing_cycle_anchor_before,
                "items": items_before,
            },
            "after": {
                "status": status_after,
                "schedule": schedule_after,
                "cancel_at": cancel_at_after,
                "cancel_at_period_end": cancel_at_period_end_after,
                "billing_cycle_anchor": billing_cycle_anchor_after,
                "items": items_after,
            },
            "checks": {
                "schedule_cleared": schedule_cleared,
                "cancel_at_cleared": cancel_at_cleared,
                "cancel_at_period_end_false": (
                    cancel_at_period_end_false
                ),
                "billing_cycle_anchor_unchanged": (
                    billing_cycle_anchor_unchanged
                ),
                "items_unchanged": items_unchanged,
                "subscription_still_billable": (
                    subscription_still_billable
                ),
            },
            "log_file": csv_path,
        }, response_status

    except stripe.error.StripeError as stripe_error:
        log_row["result_status"] = "stripe_error"
        log_row["error"] = str(stripe_error)

        csv_path = save_individual_log()

        return {
            "status": "failed",
            "subscription_id": subscription_id,
            "action": requested_action,
            "error": str(stripe_error),
            "log_file": csv_path,
        }, 500

    except Exception as error:
        log_row["result_status"] = "unexpected_error"
        log_row["error"] = str(error)

        csv_path = save_individual_log()

        return {
            "status": "failed",
            "subscription_id": subscription_id,
            "action": requested_action,
            "error": str(error),
            "log_file": csv_path,
        }, 500

# preview-subscription-item-metadata
@main.route("/admin/preview-subscription-item-metadata", methods=["GET"])
def preview_subscription_item_metadata():
    """
    Preview subscription-item metadata migration.

    This route is read-only.

    It shows which subscription items:
    - would be updated
    - are already complete
    - contain conflicting metadata
    - belong to unknown Products
    """

    if not logged_in_or_dev():
        return redirect("/login")

    collection = (
        collect_subscription_item_metadata_candidates()
    )

    results = collection["results"]
    errors = collection["errors"]

    summary = {
        "would_update": 0,
        "already_complete": 0,
        "unknown_product": 0,
        "conflicting_metadata": 0,
    }

    for result in results:
        action = result["action"]

        if action in summary:
            summary[action] += 1

    csv_path = write_subscription_item_metadata_csv(
        results=results,
        filename_prefix=(
            "subscription_item_metadata_preview"
        ),
    )

    return {
        "status": "preview_complete",
        "read_only": True,
        "checked_subscription_count": collection[
            "checked_subscription_count"
        ],
        "checked_item_count": collection[
            "checked_item_count"
        ],
        "summary": summary,
        "error_count": len(errors),
        "errors": errors[:50],
        "log_file": csv_path,
        "sample_results": results[:30],
    }

# Apply route
@main.route("/admin/apply-subscription-item-metadata", methods=["POST"])
def apply_subscription_item_metadata():
    """
    Apply item_type metadata to known subscription items.

    Required form fields:
        confirm=APPLY
        mode=test or mode=live

    Safety behavior:
    - Updates only items with action=would_update.
    - Skips already-complete items.
    - Skips unknown Products.
    - Skips conflicting metadata.
    - Preserves all existing item metadata.
    - Writes a complete CSV log.
    """

    if not logged_in_or_dev():
        return redirect("/login")

    confirm = request.form.get(
        "confirm"
    )

    if confirm != "APPLY":
        return {
            "error": (
                "Confirmation required. "
                "Submit confirm=APPLY."
            )
        }, 400

    mode = request.form.get(
        "mode"
    )

    if mode not in [
        "test",
        "live",
    ]:
        return {
            "error": (
                "Mode required. "
                "Submit mode=test or mode=live."
            )
        }, 400

    is_live_key = current_app.config[
        "STRIPE_SECRET_KEY"
    ].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": (
                "You submitted mode=live, "
                "but the configured Stripe key is not live."
            )
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": (
                "You submitted mode=test, "
                "but the configured Stripe key is live."
            )
        }, 400

    collection = (
        collect_subscription_item_metadata_candidates()
    )

    results = collection["results"]
    errors = collection["errors"]

    updated_count = 0
    already_complete_count = 0
    unknown_product_count = 0
    conflicting_metadata_count = 0
    failed_count = 0

    for result in results:

        action = result["action"]

        if action == "already_complete":
            already_complete_count += 1

            result["apply_status"] = (
                "skipped_already_complete"
            )
            result["apply_error"] = ""

            continue

        if action == "unknown_product":
            unknown_product_count += 1

            result["apply_status"] = (
                "skipped_unknown_product"
            )
            result["apply_error"] = ""

            continue

        if action == "conflicting_metadata":
            conflicting_metadata_count += 1

            result["apply_status"] = (
                "skipped_conflicting_metadata"
            )
            result["apply_error"] = ""

            continue

        if action != "would_update":
            result["apply_status"] = (
                "skipped_unrecognized_action"
            )
            result["apply_error"] = ""

            continue

        subscription_item_id = result[
            "subscription_item_id"
        ]

        merged_metadata = result[
            "merged_metadata"
        ]

        try:
            updated_item = (
                stripe.SubscriptionItem.modify(
                    subscription_item_id,
                    metadata=merged_metadata,
                )
            )

            returned_metadata = (
                stripe_metadata_to_dict(
                    stripe_get(
                        updated_item,
                        "metadata",
                        {},
                    )
                )
            )

            returned_item_type = returned_metadata.get(
                SUBSCRIPTION_ITEM_TYPE_METADATA_KEY
            )

            intended_item_type = result[
                "intended_item_type"
            ]

            if returned_item_type != intended_item_type:
                failed_count += 1

                result["apply_status"] = (
                    "verification_failed"
                )

                result["apply_error"] = (
                    "Stripe update returned unexpected "
                    f"item_type={returned_item_type!r}; "
                    f"expected {intended_item_type!r}."
                )

                errors.append({
                    "subscription_id": result[
                        "subscription_id"
                    ],
                    "subscription_item_id": (
                        subscription_item_id
                    ),
                    "customer_id": result[
                        "customer_id"
                    ],
                    "product_id": result[
                        "product_id"
                    ],
                    "error_type": (
                        "metadata_verification_failed"
                    ),
                    "error": result["apply_error"],
                })

                continue

            updated_count += 1

            result["apply_status"] = "updated"
            result["apply_error"] = ""

        except Exception as update_error:
            failed_count += 1

            result["apply_status"] = "update_failed"
            result["apply_error"] = str(update_error)

            errors.append({
                "subscription_id": result["subscription_id"],
                "subscription_item_id": subscription_item_id,
                "customer_id": result["customer_id"],
                "product_id": result["product_id"],
                "error_type": "subscription_item_metadata_update_failed",
                "error": str(update_error),
            })

    csv_path = write_subscription_item_metadata_csv(
        results=results,
        filename_prefix=(
            "subscription_item_metadata_apply"
        ),
    )

    return {
        "status": "apply_complete",
        "mode": mode,
        "checked_subscription_count": collection["checked_subscription_count"],
        "checked_item_count": collection["checked_item_count"],
        "updated_count": updated_count,
        "already_complete_count": already_complete_count,
        "unknown_product_count": unknown_product_count,
        "conflicting_metadata_count": conflicting_metadata_count,
        "failed_count": failed_count,
        "error_count": len(errors),
        "errors": errors[:50],
        "log_file": csv_path,
        "sample_results": results[:30],
    }

# debug_subscription_item
@main.route("/admin/debug-subscription-item/<subscription_item_id>", methods=["GET"])
def debug_subscription_item(subscription_item_id):
    """
    Retrieve one Stripe subscription item and display its metadata.

    Read-only route.
    """

    try:
        item = stripe.SubscriptionItem.retrieve(subscription_item_id)

        price = stripe_get(item, "price", {}) or {}

        return {
            "subscription_item_id": stripe_get(item, "id"),
            "subscription_id": stripe_get(item, "subscription"),
            "metadata": stripe_metadata_to_dict(stripe_get(item, "metadata", {})),
            "price_id": stripe_get(price, "id"),
            "product_id": stripe_get(price, "product"),
            "quantity": stripe_get(item, "quantity"),
        }

    except Exception as error:
        return {
            "error": str(error),
            "subscription_item_id": subscription_item_id,
        }, 500

# Preview migration from fixed subscription endings to open-ended ("Forever") subscriptions
@main.route("/admin/preview-contract-end-migration", methods=["GET"])
def preview_contract_end_migration():
    """
    READ-ONLY preview.

    Reviews all active, past_due, and unpaid subscriptions
    and determines what must happen to make each subscription
    open-ended.

    This route does NOT:
    - release any schedules
    - clear cancel_at
    - change cancel_at_period_end
    - modify subscriptions
    - modify metadata
    """

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # Optional:
    # Allows us to test one subscription before scanning all 536.
    #
    # Example:
    # /admin/preview-contract-end-migration?subscription_id=sub_123
    target_subscription_id = request.args.get("subscription_id")
    # request.args means: "Read parameters that come after a ? in the URL."

    results = []
    errors = []

    summary = {
        "checked_subscription_count": 0,
        "already_forever_count": 0,
        "would_clear_cancel_at_count": 0,
        "would_release_schedule_count": 0,
        "manual_review_count": 0,
        "error_count": 0,
        "would_release_equivalent_two_phase_schedule_count": 0,
    }

    # Reuse the statuses already defined for inspection-fee work:
    # active, past_due, and unpaid.
    for requested_status in INSPECTION_BILLABLE_STATUSES:
        subscriptions = stripe.Subscription.list(
            status=requested_status,
            limit=100,
        )

        for subscription in subscriptions.auto_paging_iter():
            subscription_id = stripe_get(subscription, "id")

            # If we supplied one subscription ID in the URL,
            # skip every other subscription.
            if target_subscription_id and subscription_id != target_subscription_id:
                continue

            summary["checked_subscription_count"] += 1

            # Stripe can return customer in two forms:
            #
            # Form 1: only the Customer ID
            # "cus_123"
            #
            # Form 2: the expanded Customer object
            # {
            #     "id": "cus_123",
            #     "name": "Customer Name",
            # }
            customer_reference = stripe_get(subscription, "customer")

            # isinstance(customer_reference, str) means:
            # "Is customer_reference a string?"
            #
            # If it is already a string, it is the Customer ID,
            # so we use it directly.
            if isinstance(customer_reference, str):
                customer_id = customer_reference

            # Otherwise, Stripe returned an expanded Customer object,
            # so we retrieve the ID from that object.
            else:
                customer_id = stripe_get(customer_reference, "id")

            subscription_status = stripe_get(subscription, "status")

            subscription_metadata = stripe_metadata_to_dict(
                stripe_get(subscription, "metadata", {}) or {}
            )

            contract_start_date = subscription_metadata.get("contract_start_date")
            contract_end_date = subscription_metadata.get("contract_end_date")
            inspection_fee_end_date = subscription_metadata.get("inspection_fee_end_date")

            # cancel_at is a Stripe timestamp representing the date
            # when Stripe currently plans to cancel the subscription.
            cancel_at = stripe_get(subscription, "cancel_at")

            # cancel_at_period_end should already be True or False.
            #
            # The third argument, False, means:
            # "If Stripe does not return this field, use False."
            #
            # We do not need bool(...) because Stripe already returns
            # a Boolean value for this property.
            cancel_at_period_end = stripe_get(subscription, "cancel_at_period_end", False)

            # Convert the Stripe Unix timestamp into a readable date.
            cancel_at_datetime = (stripe_timestamp_to_utc_datetime(cancel_at) if cancel_at else None)

            # Stripe may return the schedule as:
            # "sub_sched_123"
            
            # or as an expanded Schedule object
            schedule_reference = stripe_get(subscription, "schedule")

            schedule_id = None
            schedule = None
            schedule_status = None
            schedule_end_behavior = None
            schedule_phase_count = 0
            schedule_last_phase_end = None
            schedule_phases = []
            # A signature is a simplified list used to compare the billing items in every schedule phase
            schedule_phase_signatures = []
            phases = []

            # Start building the result row now.
            # We fill in the schedule information afterward.
            base_result = {
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "subscription_status": subscription_status,
                "contract_start_date": contract_start_date or "",
                "contract_end_date": contract_end_date or "",
                "inspection_fee_end_date": inspection_fee_end_date or "",
                "cancel_at": cancel_at or "",
                "cancel_at_date": cancel_at_datetime.date().isoformat() if cancel_at_datetime else "",
                "cancel_at_period_end": cancel_at_period_end,
                "schedule_id": "",
                "schedule_status": "",
                "schedule_end_behavior": "",
                "schedule_phase_count": 0,
                "schedule_last_phase_end": "",
                "schedule_phases": [],
                "all_schedule_phase_items_same": False,
                "all_phase_business_settings_same": False,
                "phase_business_differences": [],
                "action": "",
                "safe_to_apply": False,
                "reason": "",
                "error": "",
            }

            # -----------------------------------------------------
            # Retrieve and inspect the schedule
            # -----------------------------------------------------

            if schedule_reference:
                try:
                    # If Stripe returned only the schedule ID,
                    # retrieve the full schedule object.
                    if isinstance(schedule_reference, str):
                        schedule_id = schedule_reference
                        schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

                    # Otherwise, Stripe already returned an expanded schedule object
                    else:
                        schedule = schedule_reference
                        schedule_id = stripe_get(schedule, "id")

                    schedule_status = stripe_get(schedule, "status")
                    schedule_end_behavior = stripe_get(schedule, "end_behavior")

                    # phases contains all current and future instructions
                    # stored inside the schedule.
                    phases = stripe_get(schedule, "phases", []) or []

                    # len(phases) tells us how many phases exist.
                    #
                    # Your preview showed that most subscriptions have
                    # one phase, while at least one has two phases.
                    schedule_phase_count = len(phases)

                    for phase in phases:
                        phase_start_ts = stripe_get(phase, "start_date")
                        phase_end_ts = stripe_get(phase, "end_date")

                        phase_start_datetime = stripe_timestamp_to_utc_datetime(phase_start_ts) if phase_start_ts else None

                        phase_end_datetime = stripe_timestamp_to_utc_datetime(phase_end_ts) if phase_end_ts else None

                        phase_items = []
                        phase_signature = []

                        # Read all Prices contained in this phase.
                        for phase_item in stripe_get(phase, "items", []) or []:
                            phase_price_reference = stripe_get(phase_item, "price")

                            # The Price may be returned as only its ID.
                            if isinstance(phase_price_reference, str):
                                phase_price_id = phase_price_reference

                            # Or it may be returned as an expanded Price object.
                            else:
                                phase_price_id = stripe_get(phase_price_reference, "id")

                            quantity = stripe_get(phase_item, "quantity", 1)

                            phase_items.append({
                                "price_id": phase_price_id,
                                "quantity": quantity,
                            })

                            phase_signature.append({
                                "price_id": phase_price_id,
                                "quantity": quantity,
                            })

                        phase_signature.sort(
                            key=lambda item: (
                                str(item["price_id"]), 
                                item["quantity"]
                            )
                        )
                        # sort() rearranges the items in the list into a consistent order
                        # When sorting each dictionary, use its price_id first and its quantity second
                        # roughly like this
                        # def sorting_rule(item):
                            # return (
                            #     str(item["price_id"]),
                            #     item["quantity"],
                            # )

                        schedule_phase_signatures.append(phase_signature)

                        schedule_phases.append({
                            "start_date": phase_start_datetime.date().isoformat() if phase_start_datetime else "",
                            "end_date": phase_end_datetime.date().isoformat() if phase_end_datetime else "",
                            "proration_behavior": stripe_get(phase, "proration_behavior", ""),
                            "items": phase_items,
                        })

                    # Get the end date of the final phase.
                    if phases:
                        # last item
                        last_phase = phases[-1]
                        last_phase_end_ts = stripe_get(last_phase, "end_date")

                        if last_phase_end_ts:
                            last_phase_end_datetime = stripe_timestamp_to_utc_datetime(last_phase_end_ts)

                            schedule_last_phase_end = last_phase_end_datetime.date().isoformat()

                except Exception as schedule_error:
                    summary["error_count"] += 1

                    base_result["schedule_id"] = schedule_id or ""
                    base_result["action"] = "error"
                    base_result["safe_to_apply"] = False
                    base_result["reason"] = "Could not retrieve or inspect the subscription schedule."
                    base_result["error"] = str(schedule_error)

                    results.append(base_result)

                    errors.append({
                        "subscription_id": subscription_id,
                        "customer_id": customer_id,
                        "error_type": "schedule_retrieval_failed",
                        "error": str(schedule_error),
                    })

                    # Stop processing this subscription and continue
                    # with the next one.
                    continue

            all_schedule_phase_items_same = False
            all_phase_business_settings_same = False
            phase_business_differences = []
            phase_business_signatures = []

            if schedule_phase_signatures:
                first_phase_signature = schedule_phase_signatures[0]

                # all() checks a group of True/False results
                # It returns True only when every result is True
                all_schedule_phase_items_same = all(
                    phase_signature == first_phase_signature
                    for phase_signature in schedule_phase_signatures
                )
                # Compare every phase signature with the first phase signature. Return True only if every phase matches the first one
                # equivalent would be
                # all_schedule_phase_items_same = True

                # for phase_signature in schedule_phase_signatures:
                #     if phase_signature != first_phase_signature:
                #         all_schedule_phase_items_same = False
                #         break

                phase_business_signatures = [
                    build_phase_business_signature(phase)
                    for phase in phases
                ]

                first_phase_business_signature = (
                    phase_business_signatures[0]
                    if phase_business_signatures
                    else None
                )

                all_phase_business_settings_same = (
                    bool(phase_business_signatures)
                    and all(
                        signature == first_phase_business_signature
                        for signature in phase_business_signatures
                    )
                )

                if len(phase_business_signatures) > 1:
                    for phase_number, signature in enumerate(
                        phase_business_signatures[1:],
                        start=2,
                    ):
                        differing_fields = get_signature_differences(
                            first_phase_business_signature,
                            signature,
                        )

                        # to show the exact values for each differing field
                        if differing_fields:
                            field_values = {}

                            for field in differing_fields:
                                field_values[field] = {
                                    "phase_1": first_phase_business_signature[field],
                                    f"phase_{phase_number}": signature[field],
                                }

                            phase_business_differences.append({
                                "phase_number": phase_number,
                                "fields": differing_fields,
                                "values": field_values,
                            })

            # Add the schedule information to the result row.
            base_result["schedule_id"] = schedule_id or ""
            base_result["schedule_status"] = schedule_status or ""
            base_result["schedule_end_behavior"] = schedule_end_behavior or ""
            base_result["schedule_phase_count"] = schedule_phase_count
            base_result["schedule_last_phase_end"] = schedule_last_phase_end or ""
            base_result["schedule_phases"] = schedule_phases
            base_result["all_schedule_phase_items_same"] = all_schedule_phase_items_same
            base_result["all_phase_business_settings_same"] = all_phase_business_settings_same
            base_result["phase_business_differences"] = phase_business_differences

            # -----------------------------------------------------
            # Classification rules
            # -----------------------------------------------------

            # True if Stripe currently shows a cancellation timestamp.
            has_direct_cancel_at = cancel_at is not None

            # True if Stripe is configured to cancel at the end of the current billing period.
            has_cancel_at_period_end = cancel_at_period_end is True

            # True if the subscription currently has a schedule.
            has_schedule = schedule_id is not None

            # A reviewed release schedule means:
            # 1. A schedule exists.
            # 2. The schedule is active.
            # 3. Its end behavior is cancel.
            # 4. It contains exactly one phase.
            # 5. The phase items can be read and compared by Price ID and quantity.
            # 6. cancel_at_period_end is not separately enabled.
            #
            # cancel_at is allowed because Stripe commonly sets it from the final schedule end date
            is_reviewed_release_schedule = (
                has_schedule
                and schedule_status == "active"
                and schedule_end_behavior == "cancel"
                and schedule_phase_count == 1
                and all_schedule_phase_items_same
                and not has_cancel_at_period_end
            )

            is_reviewed_equivalent_two_phase_schedule = (
                has_schedule
                and schedule_status == "active"
                and schedule_end_behavior == "cancel"
                and schedule_phase_count == 2
                and all_schedule_phase_items_same
                and all_phase_business_settings_same
                and not has_cancel_at_period_end
            )

            # -----------------------------------------------------
            # Case 1: Already open-ended
            # -----------------------------------------------------

            if (
                not has_direct_cancel_at
                and not has_cancel_at_period_end
                and not has_schedule
            ):
                summary["already_forever_count"] += 1

                base_result["action"] = "already_forever"
                base_result["safe_to_apply"] = True
                base_result["reason"] = (
                    "Subscription has no cancel_at, "
                    "cancel_at_period_end is false, "
                    "and no schedule is attached."
                )

            # -----------------------------------------------------
            # Case 2: Direct cancel_at only
            # -----------------------------------------------------

            elif (
                has_direct_cancel_at
                and not has_cancel_at_period_end
                and not has_schedule
            ):
                summary["would_clear_cancel_at_count"] += 1

                base_result["action"] = "would_clear_cancel_at"
                base_result["safe_to_apply"] = True
                base_result["reason"] = (
                    "Subscription has a direct cancel_at date "
                    "and no schedule. The future apply route "
                    "could clear cancel_at while keeping the "
                    "subscription active."
                )

            # -----------------------------------------------------
            # Case 3: Safe schedule release
            # -----------------------------------------------------

            elif is_reviewed_release_schedule:
                summary["would_release_schedule_count"] += 1

                base_result["action"] = "would_release_schedule"
                base_result["safe_to_apply"] = True
                base_result["reason"] = (
                    "Subscription has one active schedule phase with "
                    "end_behavior=cancel. The schedule can be released "
                    "and the remaining cancel_at can be cleared."
                )

            elif is_reviewed_equivalent_two_phase_schedule:
                summary["would_release_equivalent_two_phase_schedule_count"] += 1

                base_result["action"] = "would_release_equivalent_two_phase_schedule"
                base_result["safe_to_apply"] = True
                base_result["reason"] = (
                    "Subscription has two equivalent schedule phases. "
                    "Only phase timing or ignored transition fields differ."
                )

            # -----------------------------------------------------
            # Case 4: Schedule requires manual review
            # -----------------------------------------------------

            elif has_schedule:
                summary["manual_review_count"] += 1

                manual_review_reasons = []

                if schedule_status != "active":
                    manual_review_reasons.append(f"schedule_status_{schedule_status}")

                if schedule_end_behavior != "cancel":
                    manual_review_reasons.append(
                        f"end_behavior_{schedule_end_behavior}"
                    )

                if schedule_phase_count < 1:
                    manual_review_reasons.append("schedule_has_no_phases")

                if (
                    schedule_phase_count > 1
                    and not all_phase_business_settings_same
                ):
                    manual_review_reasons.append(f"multiple_schedule_phases_{schedule_phase_count}")

                if (
                    schedule_phase_count > 1
                    and not all_phase_business_settings_same
                ):
                    manual_review_reasons.append(
                        "schedule_phases_have_different_business_settings"
                    )

                if not all_schedule_phase_items_same:
                    manual_review_reasons.append(
                        "schedule_phases_have_different_prices_or_quantities"
                    )

                if has_cancel_at_period_end:
                    manual_review_reasons.append("cancel_at_period_end_true")

                base_result["action"] = "manual_review"
                base_result["safe_to_apply"] = False
                base_result["reason"] = (
                    "Schedule requires manual review: "
                    + ", ".join(manual_review_reasons)
                )

            # -----------------------------------------------------
            # Case 5: Other unusual cancellation configuration
            # -----------------------------------------------------

            else:
                summary["manual_review_count"] += 1

                base_result["action"] = "manual_review"
                base_result["safe_to_apply"] = False

                ending_mechanisms = []

                if has_direct_cancel_at:
                    ending_mechanisms.append("cancel_at")

                if has_cancel_at_period_end:
                    ending_mechanisms.append("cancel_at_period_end")

                base_result["reason"] = (
                    "Subscription has an unusual ending configuration: "
                    + ", ".join(ending_mechanisms)
                    + ". Review manually before making Stripe changes."
                )

            results.append(base_result)

    # -----------------------------------------------------
    # Write CSV report
    # -----------------------------------------------------

    os.makedirs("logs", exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    csv_path = os.path.join("logs", f"contract_end_migration_preview_{timestamp}.csv")

    fieldnames = [
        "subscription_id",
        "customer_id",
        "subscription_status",
        "contract_start_date",
        "contract_end_date",
        "inspection_fee_end_date",
        "cancel_at",
        "cancel_at_date",
        "cancel_at_period_end",
        "schedule_id",
        "schedule_status",
        "schedule_end_behavior",
        "schedule_phase_count",
        "schedule_last_phase_end",
        "schedule_phases",
        "all_schedule_phase_items_same",
        "all_phase_business_settings_same",
        "phase_business_differences",
        "action",
        "safe_to_apply",
        "reason",
        "error",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for result in results:
            csv_row = result.copy()

            # A CSV cell cannot directly contain a Python list.
            # Convert schedule_phases into JSON text first.
            # json.dumps()
            # The s means: returns a String
            # Before dict -> json.dumps() -> str

            # What does json.dump() do? No s
            # Instead of returning a string,it writes directly into a file

            # loads means  JSON string  ->  Python object
            csv_row["schedule_phases"] = json.dumps(
                result.get("schedule_phases", []),
                sort_keys=True,
            )

            csv_row["phase_business_differences"] = json.dumps(
                stripe_value_to_plain_python(result.get("phase_business_differences", [])),
                sort_keys=True,
            )

            writer.writerow(csv_row)

    return {
        "status": "preview_complete",
        "read_only": True,
        "target_subscription_id": target_subscription_id,
        "statuses_checked": INSPECTION_BILLABLE_STATUSES,
        "summary": summary,
        "error_count": len(errors),
        "errors": errors[:50],
        "log_file": csv_path,
        "results": results,

        # Only return the first 30 rows in the browser response so the JSON output is not enormous
        # The complete results remain available in the CSV.
        # "sample_results": results[:30],

        # to view "manual_review" result only
        "sample_results": [
            result 
            for result in results 
            if result["action"] == "manual_review"
        ],
        # is exactly equivalent to:
        # sample_results = []

        # for result in results:
        #     if result["action"] == "manual_review":
        #         sample_results.append(result)
    }

# Read-only audit of subscriptions whose schedules are more complex
# than a simple one-phase cancel schedule
@main.route("/admin/audit-manual-review-schedules", methods=["GET"])
def audit_manual_review_schedules():
    """
    READ-ONLY audit.

    Finds subscriptions whose schedules are not simple one-phase
    cancel schedules and groups them into patterns for investigation.

    This route does NOT:
    - release schedules
    - cancel schedules
    - clear cancel_at
    - modify subscriptions
    - modify metadata
    """

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    target_subscription_id = request.args.get("subscription_id")

    results = []
    errors = []

    summary = {
        "checked_subscription_count": 0,
        "manual_review_count": 0,
        "two_phase_same_items_count": 0,
        "two_phase_different_items_count": 0,
        "three_or_more_phases_count": 0,
        "non_cancel_schedule_count": 0,
        "inactive_schedule_count": 0,
        "other_pattern_count": 0,
        "error_count": 0,
    }

    product_cache = {}

    for requested_status in INSPECTION_BILLABLE_STATUSES:
        subscriptions = stripe.Subscription.list(
            status=requested_status,
            limit=100,
        )

        for subscription in subscriptions.auto_paging_iter():
            subscription_id = stripe_get(subscription, "id")

            if target_subscription_id and subscription_id != target_subscription_id:
                continue

            summary["checked_subscription_count"] += 1

            schedule_reference = stripe_get(subscription, "schedule")

            # This route only investigates subscriptions with schedules.
            if not schedule_reference:
                continue

            customer_reference = stripe_get(subscription, "customer")

            if isinstance(customer_reference, str):
                customer_id = customer_reference
            else:
                customer_id = stripe_get(customer_reference, "id")

            try:
                # Stripe may return only the schedule ID or an expanded object.
                if isinstance(schedule_reference, str):
                    schedule_id = schedule_reference
                    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)
                else:
                    schedule = schedule_reference
                    schedule_id = stripe_get(schedule, "id")

                schedule_status = stripe_get(schedule, "status")
                schedule_end_behavior = stripe_get(schedule, "end_behavior")
                phases = stripe_get(schedule, "phases", []) or []
                phase_count = len(phases)

                cancel_at = stripe_get(subscription, "cancel_at")
                cancel_at_datetime = (
                    stripe_timestamp_to_utc_datetime(cancel_at)
                    if cancel_at
                    else None
                )

                metadata = stripe_metadata_to_dict(
                    stripe_get(subscription, "metadata", {}) or {}
                )

                # This is the same rule used by the main preview route.
                is_simple_cancel_schedule = (
                    schedule_status == "active"
                    and schedule_end_behavior == "cancel"
                    and phase_count == 1
                    and stripe_get(subscription, "cancel_at_period_end", False) is False
                )

                # Skip the 506 simple schedules.
                if is_simple_cancel_schedule:
                    continue

                summary["manual_review_count"] += 1

                customer_name = None
                customer_email = None
                customer_description = None

                try:
                    customer = stripe.Customer.retrieve(customer_id)

                    customer_name = stripe_get(customer, "name")
                    customer_email = stripe_get(customer, "email")
                    customer_description = stripe_get(customer, "description")

                except Exception as customer_error:
                    errors.append({
                        "subscription_id": subscription_id,
                        "schedule_id": schedule_id,
                        "customer_id": customer_id,
                        "error_type": "customer_retrieval_failed",
                        "error": str(customer_error),
                    })

                phase_details = []
                phase_item_signatures = []

                for phase_index, phase in enumerate(phases, start=1):
                    phase_start_ts = stripe_get(phase, "start_date")
                    phase_end_ts = stripe_get(phase, "end_date")

                    phase_start_datetime = (
                        stripe_timestamp_to_utc_datetime(phase_start_ts)
                        if phase_start_ts
                        else None
                    )

                    phase_end_datetime = (
                        stripe_timestamp_to_utc_datetime(phase_end_ts)
                        if phase_end_ts
                        else None
                    )

                    phase_duration_days = None

                    if phase_start_datetime and phase_end_datetime:
                        phase_duration_days = (
                            phase_end_datetime.date()
                            - phase_start_datetime.date()
                        ).days

                    phase_items = []
                    phase_signature = []

                    for phase_item in stripe_get(phase, "items", []) or []:
                        price_reference = stripe_get(phase_item, "price")

                        if isinstance(price_reference, str):
                            price_id = price_reference
                            price = stripe.Price.retrieve(price_id)
                        else:
                            price = price_reference or {}
                            price_id = stripe_get(price, "id")

                        product_reference = stripe_get(price, "product")

                        if isinstance(product_reference, str):
                            product_id = product_reference

                            if product_id not in product_cache:
                                product_cache[product_id] = stripe.Product.retrieve(product_id)

                            product = product_cache[product_id]
                        else:
                            product = product_reference or {}
                            product_id = stripe_get(product, "id")

                        product_name = stripe_get(product, "name")
                        product_metadata = stripe_metadata_to_dict(
                            stripe_get(product, "metadata", {}) or {}
                        )

                        item_type = determine_subscription_item_type(
                            product_id=product_id,
                            product_metadata=product_metadata,
                        )

                        quantity = stripe_get(phase_item, "quantity", 1)
                        unit_amount = stripe_get(price, "unit_amount")
                        currency = stripe_get(price, "currency")

                        phase_items.append({
                            "price_id": price_id,
                            "product_id": product_id,
                            "product_name": product_name,
                            "item_type": item_type or "unknown",
                            "quantity": quantity,
                            "unit_amount": unit_amount,
                            "currency": currency,
                        })

                        # This signature lets us compare phase contents.
                        phase_signature.append({
                            "product_id": product_id,
                            "price_id": price_id,
                            "quantity": quantity,
                        })

                    # Sort so item order does not affect the comparison.
                    phase_signature.sort(
                        key=lambda item: (
                            str(item["product_id"]),
                            str(item["price_id"]),
                            item["quantity"],
                        )
                    )

                    phase_item_signatures.append(phase_signature)

                    phase_details.append({
                        "phase_number": phase_index,
                        "start_date": (
                            phase_start_datetime.date().isoformat()
                            if phase_start_datetime
                            else ""
                        ),
                        "end_date": (
                            phase_end_datetime.date().isoformat()
                            if phase_end_datetime
                            else ""
                        ),
                        "duration_days": phase_duration_days,
                        "proration_behavior": stripe_get(
                            phase,
                            "proration_behavior",
                            "",
                        ),
                        "billing_cycle_anchor": stripe_get(
                            phase,
                            "billing_cycle_anchor",
                            "",
                        ),
                        "item_count": len(phase_items),
                        "items": phase_items,
                    })

                all_phase_items_same = False

                if phase_item_signatures:
                    first_signature = phase_item_signatures[0]

                    all_phase_items_same = all(
                        signature == first_signature
                        for signature in phase_item_signatures
                    )

                first_phase_duration_days = (
                    phase_details[0]["duration_days"]
                    if phase_details
                    else None
                )

                short_first_phase = (
                    first_phase_duration_days is not None
                    and first_phase_duration_days <= 31
                )

                # Classify the complex schedule into a more useful pattern.
                if schedule_status != "active":
                    pattern = "inactive_schedule"
                    recommended_action = (
                        "Review schedule status manually before deciding "
                        "whether any Stripe action is required."
                    )
                    summary["inactive_schedule_count"] += 1

                elif schedule_end_behavior != "cancel":
                    pattern = "non_cancel_schedule"
                    recommended_action = (
                        "Schedule does not end with cancel. Review its purpose "
                        "before changing or releasing it."
                    )
                    summary["non_cancel_schedule_count"] += 1

                elif phase_count == 2 and all_phase_items_same:
                    pattern = "two_phase_same_items"
                    recommended_action = (
                        "Both phases contain the same items and Prices. "
                        "The extra phase may only represent a timing or billing "
                        "adjustment. Review dates and proration before release."
                    )
                    summary["two_phase_same_items_count"] += 1

                elif phase_count == 2 and not all_phase_items_same:
                    pattern = "two_phase_different_items"
                    recommended_action = (
                        "The phases contain different items or Prices. "
                        "Do not release until the future change is understood."
                    )
                    summary["two_phase_different_items_count"] += 1

                elif phase_count >= 3:
                    pattern = "three_or_more_phases"
                    recommended_action = (
                        "Schedule contains three or more phases. "
                        "Review every future phase manually."
                    )
                    summary["three_or_more_phases_count"] += 1

                else:
                    pattern = "other_pattern"
                    recommended_action = (
                        "Schedule does not match a known pattern. "
                        "Review manually."
                    )
                    summary["other_pattern_count"] += 1

                results.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "customer_email": customer_email,
                    "customer_description": customer_description,
                    "subscription_status": stripe_get(subscription, "status"),
                    "contract_start_date": metadata.get("contract_start_date", ""),
                    "contract_end_date": metadata.get("contract_end_date", ""),
                    "inspection_fee_end_date": metadata.get(
                        "inspection_fee_end_date",
                        "",
                    ),
                    "cancel_at": cancel_at or "",
                    "cancel_at_date": (
                        cancel_at_datetime.date().isoformat()
                        if cancel_at_datetime
                        else ""
                    ),
                    "cancel_at_period_end": stripe_get(
                        subscription,
                        "cancel_at_period_end",
                        False,
                    ),
                    "schedule_id": schedule_id,
                    "schedule_status": schedule_status,
                    "schedule_end_behavior": schedule_end_behavior,
                    "phase_count": phase_count,
                    "all_phase_items_same": all_phase_items_same,
                    "short_first_phase": short_first_phase,
                    "first_phase_duration_days": first_phase_duration_days,
                    "pattern": pattern,
                    "safe_to_release": False,
                    "recommended_action": recommended_action,
                    "phases": phase_details,
                    "error": "",
                })

            except Exception as schedule_error:
                summary["error_count"] += 1

                errors.append({
                    "subscription_id": subscription_id,
                    "customer_id": customer_id,
                    "error_type": "complex_schedule_audit_failed",
                    "error": str(schedule_error),
                })

    os.makedirs("logs", exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    csv_path = os.path.join(
        "logs",
        f"manual_review_schedule_audit_{timestamp}.csv",
    )

    fieldnames = [
        "subscription_id",
        "customer_id",
        "customer_name",
        "customer_email",
        "customer_description",
        "subscription_status",
        "contract_start_date",
        "contract_end_date",
        "inspection_fee_end_date",
        "cancel_at",
        "cancel_at_date",
        "cancel_at_period_end",
        "schedule_id",
        "schedule_status",
        "schedule_end_behavior",
        "phase_count",
        "all_phase_items_same",
        "short_first_phase",
        "first_phase_duration_days",
        "pattern",
        "safe_to_release",
        "recommended_action",
        "phases",
        "error",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for result in results:
            csv_row = result.copy()
            csv_row["phases"] = json.dumps(
                result.get("phases", []),
                sort_keys=True,
            )
            writer.writerow(csv_row)

    return {
        "status": "audit_complete",
        "read_only": True,
        "target_subscription_id": target_subscription_id,
        "summary": summary,
        "error_count": len(errors),
        "errors": errors[:50],
        "log_file": csv_path,
        "results": results,
    }

# Apply one contract-end migration action only.
# This route is intentionally limited to one subscription per request.
@main.route("/admin/apply-contract-end-migration-one/<subscription_id>", methods=["POST"])
def apply_contract_end_migration_one(subscription_id):
    """
    LIVE write route for one subscription only.

    Supported actions:
    - release_schedule
    - clear_cancel_at

    Every attempt creates a one-row CSV audit log.
    """

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")
    requested_action = request.form.get("action")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    if mode not in ["test", "live"]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    if requested_action not in ["release_schedule", "clear_cancel_at"]:
        return {
            "error": (
                "Invalid action. Submit action=release_schedule "
                "or action=clear_cancel_at."
            )
        }, 400

    response_body, response_status = (
        apply_contract_end_migration_internal(
            subscription_id=subscription_id,
            requested_action=requested_action,
            mode=mode,
            write_individual_log=True,
        )
    )

    return response_body, response_status

@main.route("/admin/debug-subscription-schedule/<schedule_id>")
def debug_subscription_schedule(schedule_id):
    if not session.get("logged_in"):
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)

    phases = stripe_get(schedule, "phases", []) or []

    phase_results = []

    for index, phase in enumerate(phases, start=1):
        phase_items = []

        for phase_item in stripe_get(phase, "items", []) or []:
            price_reference = stripe_get(phase_item, "price")

            if isinstance(price_reference, str):
                price_id = price_reference
            else:
                price_id = stripe_get(price_reference, "id")

            phase_items.append({
                "price_id": price_id,
                "quantity": stripe_get(phase_item, "quantity", 1),
            })

        phase_results.append({
            "phase_number": index,
            "start_date": stripe_get(phase, "start_date"),
            "end_date": stripe_get(phase, "end_date"),
            "proration_behavior": stripe_get(phase, "proration_behavior"),
            "items": phase_items,
        })

    return {
        "schedule_id": stripe_get(schedule, "id"),
        "subscription_id": stripe_get(schedule, "subscription"),
        "status": stripe_get(schedule, "status"),
        "end_behavior": stripe_get(schedule, "end_behavior"),
        "phase_count": len(phases),
        "phases": phase_results,
    }

def get_contract_end_migration_item_snapshot(subscription):
    items_object = stripe_get(subscription, "items", {})
    subscription_items = stripe_get(items_object, "data", []) or []

    item_snapshot = []

    for item in subscription_items:
        price = stripe_get(item, "price", {}) or {}

        item_snapshot.append({
            "subscription_item_id": stripe_get(item, "id"),
            "price_id": stripe_get(price, "id"),
            "quantity": stripe_get(item, "quantity", 1),
        })

    item_snapshot.sort(
        key=lambda item: (
            str(item["subscription_item_id"]),
            str(item["price_id"]),
            item["quantity"],
        )
    )

    return item_snapshot


def write_contract_end_migration_log(log_row):
    os.makedirs("logs", exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")

    csv_path = os.path.join(
        "logs",
        f"contract_end_migration_one_{timestamp}.csv",
    )

    fieldnames = [
        "migrated_at",
        "mode",
        "action",
        "subscription_id",
        "customer_id",
        "schedule_id_before",
        "status_before",
        "status_after",
        "cancel_at_before",
        "cancel_at_after",
        "cancel_at_period_end_before",
        "cancel_at_period_end_after",
        "billing_cycle_anchor_before",
        "billing_cycle_anchor_after",
        "items_before",
        "items_after",
        "schedule_cleared",
        "cancel_at_cleared",
        "cancel_at_period_end_false",
        "billing_cycle_anchor_unchanged",
        "items_unchanged",
        "subscription_still_billable",
        "verification_passed",
        "result_status",
        "error",
    ]

    csv_row = log_row.copy()
    csv_row["items_before"] = json.dumps(
        log_row.get("items_before", []),
        sort_keys=True,
    )
    csv_row["items_after"] = json.dumps(
        log_row.get("items_after", []),
        sort_keys=True,
    )

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(csv_row)

    return csv_path

# bulk apply
@main.route("/admin/apply-contract-end-migration-all", methods=["POST"])
def apply_contract_end_migration_all():
    """
    Apply the contract-end migration to safe subscriptions only.

    Required form fields:
    - confirm=APPLY
    - mode=test or mode=live
    - max_apply=number

    Safe preview actions:
    - would_release_schedule
    - would_release_equivalent_two_phase_schedule
    - would_clear_cancel_at

    Everything else is skipped.

    max_apply allows a controlled batch rollout.
    Examples:
    - max_apply=5
    - max_apply=25
    - max_apply=530
    """

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    mode = request.form.get("mode")

    if mode not in [
        "test",
        "live",
    ]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    # ---------------------------------------------------------
    # Validate controlled batch size
    # ---------------------------------------------------------

    max_apply_raw = request.form.get("max_apply")

    if not max_apply_raw:
        return {
            "error": "max_apply is required. For example, submit max_apply=5."
        }, 400

    try:
        max_apply = int(max_apply_raw)

    except ValueError:
        return {
            "error": "max_apply must be a whole number."
        }, 400

    if max_apply < 1:
        return {
            "error": "max_apply must be at least 1."
        }, 400

    # Prevent an accidental absurd value
    if max_apply > 1000:
        return {
            "error": "max_apply cannot be greater than 1000."
        }, 400

    # ---------------------------------------------------------
    # Run a fresh preview immediately before applying
    # ---------------------------------------------------------
    #
    # This uses current Stripe data.
    #
    # The preview route returns a normal Python dictionary, so
    # we can call it here as a function.
    #
    # It will also create a fresh preview CSV, which gives us a
    # pre-migration audit snapshot.
    # ---------------------------------------------------------

    preview_data = preview_contract_end_migration()

    # Defensive check in case the preview function ever returns
    # a Flask response tuple instead of only a dictionary.
    if isinstance(preview_data, tuple):
        preview_body = preview_data[0]
        preview_status = preview_data[1]

        if preview_status != 200:
            return {
                "status": "failed",
                "error": "Fresh preview failed before bulk migration.",
                "preview_response": preview_body,
            }, 500

        preview_data = preview_body

    preview_results = preview_data.get("results", [])

    preview_summary = preview_data.get("summary", {})

    preview_log_file = preview_data.get("log_file", "")

    # This prevents the route from falsely returning "completed" with zero attempts again
    expected_checked_count = preview_summary.get(
        "checked_subscription_count",
        0,
    )

    if len(preview_results) != expected_checked_count:
        return {
            "status": "stopped",
            "reason": "Preview result rows do not match the preview summary. No subscriptions were modified.",
            "preview_result_count": len(preview_results),
            "expected_checked_count": expected_checked_count,
            "preview_log_file": preview_log_file,
        }, 500

    # Do not perform writes if the fresh preview found errors.
    preview_error_count = preview_data.get("error_count", preview_summary.get("error_count", 0))

    if preview_error_count != 0:
        return {
            "status": "stopped",
            "reason": "Fresh preview contains errors. No subscriptions were modified.",
            "preview_error_count": preview_error_count,
            "preview_log_file": preview_log_file,
            "preview_summary": preview_summary,
        }, 400

    # ---------------------------------------------------------
    # Define exactly which classifications are safe
    # ---------------------------------------------------------

    release_actions = {
        "would_release_schedule",
        "would_release_equivalent_two_phase_schedule",
    }

    clear_cancel_at_action = "would_clear_cancel_at"

    safe_preview_actions = release_actions | {clear_cancel_at_action}

    # ---------------------------------------------------------
    # Counters and result storage
    # ---------------------------------------------------------

    run_id = str(uuid.uuid4())

    started_at = datetime.now(timezone.utc).isoformat()

    checked_count = len(preview_results)

    eligible_count = 0
    attempted_count = 0
    success_count = 0
    failed_count = 0
    rejected_count = 0
    skipped_count = 0

    action_counts = {
        "release_schedule": 0,
        "clear_cancel_at": 0,
    }

    skip_counts = {
        "already_forever": 0,
        "manual_review": 0,
        "error": 0,
        "not_safe_to_apply": 0,
        "unrecognized_action": 0,
        "batch_limit_reached": 0,
    }

    results = []

    # ---------------------------------------------------------
    # Process each fresh preview result
    # ---------------------------------------------------------

    for candidate in preview_results:

        subscription_id = candidate.get("subscription_id")

        customer_id = candidate.get("customer_id")

        preview_action = candidate.get("action")

        safe_to_apply = candidate.get("safe_to_apply", False)

        # -----------------------------------------------------
        # Skip everything not approved by preview
        # -----------------------------------------------------

        if preview_action not in safe_preview_actions:

            skipped_count += 1

            if preview_action == "already_forever":
                skip_reason = "already_forever"
                skip_counts["already_forever"] += 1

            elif preview_action == "manual_review":
                skip_reason = "manual_review"
                skip_counts["manual_review"] += 1

            elif preview_action == "error":
                skip_reason = "error"
                skip_counts["error"] += 1

            else:
                skip_reason = "unrecognized_action"
                skip_counts[
                    "unrecognized_action"
                ] += 1

            results.append({
                "run_id": run_id,
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "preview_action": preview_action,
                "safe_to_apply": safe_to_apply,
                "requested_action": "",
                "status": "skipped",
                "reason": skip_reason,
                "verification_passed": False,
                "http_status": "",
                "individual_log_file": "",
                "error": "",
            })

            continue

        # Even if the action name is recognized, require the
        # explicit safety Boolean from the preview.
        if safe_to_apply is not True:

            skipped_count += 1
            skip_counts["not_safe_to_apply"] += 1

            results.append({
                "run_id": run_id,
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "preview_action": preview_action,
                "safe_to_apply": safe_to_apply,
                "requested_action": "",
                "status": "skipped",
                "reason": "not_safe_to_apply",
                "verification_passed": False,
                "http_status": "",
                "individual_log_file": "",
                "error": "",
            })

            continue

        eligible_count += 1

        # -----------------------------------------------------
        # Stop applying when the requested batch limit is met
        # -----------------------------------------------------

        if attempted_count >= max_apply:

            skipped_count += 1
            skip_counts["batch_limit_reached"] += 1

            results.append({
                "run_id": run_id,
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "preview_action": preview_action,
                "safe_to_apply": safe_to_apply,
                "requested_action": "",
                "status": "skipped",
                "reason": "batch_limit_reached",
                "verification_passed": False,
                "http_status": "",
                "individual_log_file": "",
                "error": "",
            })

            continue

        # -----------------------------------------------------
        # Translate preview action into write action
        # -----------------------------------------------------

        if preview_action in release_actions:
            requested_action = "release_schedule"

        else:
            requested_action = "clear_cancel_at"

        attempted_count += 1
        action_counts[requested_action] += 1

        # -----------------------------------------------------
        # Call the shared one-subscription helper
        # -----------------------------------------------------

        try:
            response_body, response_status = apply_contract_end_migration_internal(
                subscription_id=subscription_id,
                requested_action=requested_action,
                mode=mode,
                write_individual_log=False,
            )

            verification_passed = response_body.get("verification_passed", False)

            result_status = response_body.get("status", "unknown")

            individual_log_file = response_body.get("log_file", "")

            error_message = response_body.get("error", "")

            if (
                response_status == 200
                and verification_passed is True
            ):
                success_count += 1

            elif result_status == "rejected":
                rejected_count += 1

            else:
                failed_count += 1

            results.append({
                "run_id": run_id,
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "preview_action": preview_action,
                "safe_to_apply": safe_to_apply,
                "requested_action": requested_action,
                "status": result_status,
                "reason": "",
                "verification_passed": (
                    verification_passed
                ),
                "http_status": response_status,
                "individual_log_file": (
                    individual_log_file
                ),
                "error": error_message,
            })

        except Exception as error:
            failed_count += 1

            results.append({
                "run_id": run_id,
                "subscription_id": subscription_id,
                "customer_id": customer_id,
                "preview_action": preview_action,
                "safe_to_apply": safe_to_apply,
                "requested_action": requested_action,
                "status": "unexpected_bulk_error",
                "reason": "",
                "verification_passed": False,
                "http_status": 500,
                "individual_log_file": "",
                "error": str(error),
            })

    # ---------------------------------------------------------
    # Write one bulk summary CSV
    # ---------------------------------------------------------

    os.makedirs(
        "logs",
        exist_ok=True,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")

    bulk_log_path = os.path.join("logs", f"contract_end_migration_bulk_{timestamp}.csv")

    fieldnames = [
        "run_id",
        "subscription_id",
        "customer_id",
        "preview_action",
        "safe_to_apply",
        "requested_action",
        "status",
        "reason",
        "verification_passed",
        "http_status",
        "individual_log_file",
        "error",
    ]

    with open(bulk_log_path, "w", newline="", encoding="utf-8-sig") as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(results)

    finished_at = datetime.now(timezone.utc).isoformat()

    # ---------------------------------------------------------
    # Return a manageable response
    # ---------------------------------------------------------

    return {
        "status": "completed" if failed_count == 0 and rejected_count == 0 else "completed_with_issues",
        "run_id": run_id,
        "mode": mode,
        "max_apply": max_apply,
        "started_at": started_at,
        "finished_at": finished_at,
        "fresh_preview_log_file": preview_log_file,
        "bulk_log_file": bulk_log_path,
        "fresh_preview_summary": preview_summary,
        "checked_count": checked_count,
        "eligible_count": eligible_count,
        "attempted_count": attempted_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "rejected_count": rejected_count,
        "skipped_count": skipped_count,
        "action_counts": action_counts,
        "skip_counts": skip_counts,
        "issue_results": [
            result
            for result in results
            if result["status"] not in [
                "verified_success",
                "skipped",
            ]
        ][:50],
        "sample_success_results": [
            result
            for result in results
            if result["status"]
            == "verified_success"
        ][:20],
    }

# Classification of inspection fee 
def classify_expired_inspection_fee_subscription(subscription, customer_description, today):
    billable_statuses = {
        "active",
        "past_due",
        "unpaid"
    }

    metadata = stripe_metadata_to_dict(stripe_get(subscription, "metadata", {}) or {})
    # inspection_fee_end_date_raw contains text (it is a string), not a date
    # and strings do not have .isoformat()
    inspection_fee_end_date_raw = metadata.get("inspection_fee_end_date")

    inspection_fee_status = metadata.get("inspection_fee_status")
    status = stripe_get(subscription, "status")

    inspection_items = []
    monthly_service_items = []
    other_items = []

    subscription_items = stripe_get(subscription, "items", {}) or {}
    item_data = stripe_get(subscription_items, "data", []) or []

    for item in item_data:
        item_metadata = stripe_metadata_to_dict(stripe_get(item, "metadata", {}) or {})
        item_type = item_metadata.get("item_type")

        if item_type == "inspection_fee":
            inspection_items.append(item)
        elif item_type == "monthly_service_fee": 
            monthly_service_items.append(item)
        else:
            other_items.append(item)

    subscription_id = stripe_get(subscription, "id")

    customer = stripe_get(subscription, "customer")

    if isinstance(customer, str):
        customer_id = customer
    else: 
        customer_id = stripe_get(customer, "id")

    schedule = stripe_get(subscription, "schedule")
    cancel_at = stripe_get(subscription, "cancel_at")
    cancel_at_period_end = stripe_get(subscription, "cancel_at_period_end")
    billing_cycle_anchor = stripe_get(subscription, "billing_cycle_anchor")
    inspection_item_id = None

    if schedule is None or isinstance(schedule, str):
        schedule_id = schedule
    else:
        schedule_id = stripe_get(schedule, "id")

    result = {
        "subscription_id": subscription_id,
        "customer_id": customer_id,
        "subscription_status": status,
        "inspection_fee_end_date": inspection_fee_end_date_raw,
        "inspection_fee_status": inspection_fee_status,
        "inspection_item_count": len(inspection_items),
        "monthly_service_item_count": len(monthly_service_items),
        "other_item_count": len(other_items),
        "classification": None,
        "safe_to_apply": False,
        "reason": None,
        "schedule_id": schedule_id,
        "cancel_at": cancel_at,
        "cancel_at_period_end": cancel_at_period_end,
        "billing_cycle_anchor": billing_cycle_anchor,
        "inspection_item_id": inspection_item_id
    }

    if status not in billable_statuses:
        result["classification"] = "manual_review"
        result["reason"] = f"Subscription status is not billable: {status}"

        return result
    
    if schedule_id:
        result["classification"] = "manual_review"
        result["reason"] = f"Attached schedule exists: {schedule_id}"

        return result

    if cancel_at is not None or cancel_at_period_end is True:
        result["classification"] = "manual_review"
        result["reason"] = "Subscription is not fully open-ended."

        return result
    
    # Ottawa rule
    normalized_description = (customer_description or "").strip()
    is_ottawa = normalized_description.startswith("3")

    if is_ottawa and len(inspection_items) >= 1:
        result["classification"] = "manual_review"
        result["reason"] = "Ottawa subscriptions should not contain an inspection fee item. "

        return result
    
    if is_ottawa and len(inspection_items) == 0:
        result["classification"] = "not_applicable"
        result["reason"] = "Ottawa subscription correctly has no inspection fee item."

        return result

    if not inspection_fee_end_date_raw: 
        result["classification"] = "manual_review"
        result["reason"] = "Inspection fee end date is missing. "

        return result

    try: 
        # convert string into an actual date object
        # asking the date class to take this string and create a date object from it
        inspection_fee_end_date = date.fromisoformat(inspection_fee_end_date_raw)

    except (TypeError, ValueError):
        result["classification"] = "manual_review"
        result["reason"] = f"Invalid inspection fee end date: {inspection_fee_end_date_raw}"

        return result
    
    # .isoformat() converts the date into text
    result["inspection_fee_end_date"] = inspection_fee_end_date.isoformat()

    if today < inspection_fee_end_date:
        result["classification"] = "not_due_yet"
        result["reason"] = f"Inspection fee does not expire until {inspection_fee_end_date.isoformat()}"

        return result
    
    # distinguish already_removed and manual_review
    if (
        len(inspection_items) == 0
        and len(monthly_service_items) == 1
        and len(other_items) == 0
        and inspection_fee_status == "removed"
    ):
        result["classification"] = "already_removed"
        result["reason"] = "Inspection fee was already removed."

        return result

    if len(monthly_service_items) != 1:
        result["classification"] = "manual_review"
        result["reason"] = f"Expected exactly 1 monthly service item, but found {len(monthly_service_items)}."

        return result
    
    if len(inspection_items) != 1:
        result["classification"] = "manual_review"
        result["reason"] = f"Expected exactly 1 inspection fee item, but found {len(inspection_items)}."

        return result

    if len(other_items) != 0:
        result["classification"] = "manual_review"
        result["reason"] = f"Expected 0 unrecognized subscription items, but found {len(other_items)}."

        return result
    
    inspection_item = inspection_items[0]
    inspection_item_id = stripe_get(inspection_item, "id")

    result["inspection_item_id"] = inspection_item_id

    if inspection_fee_status != "active":
        result["classification"] = "manual_review"
        result["reason"] = f"Expected inspection_fee_status to be active, but found: {inspection_fee_status!r}"
        # The !r makes missing or unusual values obvious: None, '', 'ACTIVE'

        return result

    result["classification"] = "would_remove_inspection_fee"
    result["safe_to_apply"] = True
    result["reason"] = "Inspection fee has expired and is eligible for removal."

    return result

# Preview route of inspection fee
@main.route("/admin/preview-expired-inspection-fees")
def preview_expired_inspection_fees():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    target_subscription_id = request.args.get("subscription_id")
    today = datetime.now(timezone.utc).date()

    billable_statuses = [
        "active",
        "past_due",
        "unpaid",
    ]

    results = []

    classification_counts = {
        "would_remove_inspection_fee": 0,
        "not_due_yet": 0,
        "not_applicable": 0,
        "manual_review": 0,
        "already_removed": 0,
    }

    # a targeted subscription
    if target_subscription_id: 
        subscription = stripe.Subscription.retrieve(
            target_subscription_id,
            expand=["customer"]
        )

        customer = stripe_get(subscription, "customer")

        customer_description = stripe_get(customer, "description", "")

        result = classify_expired_inspection_fee_subscription(
            subscription=subscription,
            customer_description=customer_description,
            today=today,
        )

        results.append(result)

        classification = result["classification"]

        # The second argument in .get() is a default value
        # Give me the current count for this classification. If no count exists yet, pretend its current count is zero
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        # same as classification_counts[classification] += 1 
        # but it will crash if the classifier ever returns an unexpected classification. The .get(classification, 0) version is more defensive.

    # For the bulk path
    else:
        for status in billable_statuses:
            subscriptions = stripe.Subscription.list(
                status=status,
                limit=100,
                expand=["data.customer"],
            )

            for subscription in subscriptions.auto_paging_iter():
                customer = stripe_get(subscription, "customer")

                customer_description = stripe_get(customer, "description", "")

                result = classify_expired_inspection_fee_subscription(
                    subscription=subscription,
                    customer_description=customer_description,
                    today=today,
                )

                results.append(result)

                classification = result["classification"]

                classification_counts[classification] = classification_counts.get(classification, 0) + 1

    safe_to_apply_count = sum(1 for result in results if result["safe_to_apply"] is True)

    return {
        "read_only": True,
        "target_subscription_id": target_subscription_id,
        "today": today.isoformat(),
        "checked_count": len(results),
        "safe_to_apply_count": safe_to_apply_count,
        "classification_counts": classification_counts,
        "results": results,
    }

# apply_expired_inspection_fee_internal
def apply_expired_inspection_fee_internal(subscription_id, mode):
    allowed_modes = {
        "test",
        "live",
    }

    if not subscription_id:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "input_validation",
            "reason": "Subscription ID is required.",
        }

    if mode not in allowed_modes:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "input_validation",
            "reason": f"Unsupported mode: {mode!r}",
        }

    try:
        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["customer"],
        )

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "subscription_retrieval",
            "reason": str(exc),
        }

    customer = stripe_get(subscription, "customer")

    customer_description = stripe_get(customer, "description", "")

    today = datetime.now(timezone.utc).date()

    classification_result = classify_expired_inspection_fee_subscription(
        subscription=subscription,
        customer_description=customer_description,
        today=today,
    )

    if (
        classification_result["classification"] != "would_remove_inspection_fee"
        or classification_result["safe_to_apply"] is not True
    ):
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "pre_write_validation",
            "reason": classification_result["reason"],
            "classification": classification_result["classification"],
            "safe_to_apply": classification_result["safe_to_apply"],
            "inspection_item_id": classification_result["inspection_item_id"],
            "before": classification_result,
        }

    inspection_item_id = classification_result["inspection_item_id"]

    if not inspection_item_id:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "pre_write_validation",
            "reason": "Classifier marked the subscription safe, but no inspection item ID was returned.",
            "classification": classification_result["classification"],
            "safe_to_apply": classification_result["safe_to_apply"],
            "inspection_item_id": None,
            "before": classification_result,
        }

    try: 
        deleted_item = stripe.SubscriptionItem.delete(inspection_item_id, proration_behavior="none")

    except stripe.error.StripeError as exc: 
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "inspection_item_deletion",
            "inspection_item_id": inspection_item_id,
            "before": classification_result,
            "reason": str(exc),
            "writes": {
                "inspection_item_deleted": False,
                "metadata_updated": False,
            },
        }
    
    deleted = stripe_get(deleted_item, "deleted")

    if deleted is not True:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "inspection_item_deletion",
            "reason": "Subscription item deletion was not confirmed by Stripe.",
            "inspection_item_id": inspection_item_id,
            "before": classification_result,
            "writes": {
                "inspection_item_deleted": False,
                "metadata_updated": False,
            },
        }

    # update metadata
    try: 
        stripe.Subscription.modify(
            subscription_id,
            metadata={
                "inspection_fee_status": "removed",
            })

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "partial_failure",
            "stage": "metadata_update",
            "reason": str(exc),
            "inspection_item_id": inspection_item_id,
            "before": classification_result,
            "writes": {
                "inspection_item_deleted": True,
                "metadata_updated": False,
            },
            "requires_metadata_repair": True,
        }

    # retrieve subscription again
    try:
        final_subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["customer"],
        )

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "verification_failed",
            "stage": "post_write_retrieval",
            "reason": str(exc),
            "inspection_item_id": inspection_item_id,
            "before": classification_result,
            "writes": {
                "inspection_item_deleted": True,
                "metadata_updated": True,
            },
            "requires_manual_review": True,
        }

    # verify the final subscription
    final_metadata = stripe_metadata_to_dict(stripe_get(final_subscription, "metadata", {}) or {})
    final_status = stripe_get(final_subscription, "status")

    final_items = stripe_get(final_subscription, "items", {}) or {}
    final_item_data = stripe_get(final_items, "data", []) or []
    
    final_inspection_items = []
    final_monthly_service_items = []
    final_other_items = []

    # final_item_data is already a list, so don’t call it like a function
    for item in final_item_data:
        item_metadata = stripe_metadata_to_dict(stripe_get(item, "metadata", {}) or {})
        item_type = item_metadata.get("item_type")

        if item_type == "inspection_fee":
            final_inspection_items.append(item)
        elif item_type == "monthly_service_fee": 
            final_monthly_service_items.append(item)
        else:
            final_other_items.append(item)

    final_inspection_fee_status = final_metadata.get("inspection_fee_status")

    billable_statuses = {
        "active",
        "past_due",
        "unpaid",
    }

    inspection_item_count_is_zero = len(final_inspection_items) == 0

    monthly_service_item_count_is_one = len(final_monthly_service_items) == 1

    other_item_count_is_zero = len(final_other_items) == 0

    inspection_fee_status_is_removed = final_inspection_fee_status == "removed"

    subscription_remains_billable = final_status in billable_statuses

    # use all(...) Because it returns True only when every Boolean inside is True
    verification_passed = all([
        inspection_item_count_is_zero,
        monthly_service_item_count_is_one,
        other_item_count_is_zero,
        inspection_fee_status_is_removed,
        subscription_remains_billable,
    ])

    verification = {
        "inspection_item_count_is_zero": inspection_item_count_is_zero,
        "monthly_service_item_count_is_one": monthly_service_item_count_is_one,
        "other_item_count_is_zero": other_item_count_is_zero,
        "inspection_fee_status_is_removed": inspection_fee_status_is_removed,
        "subscription_remains_billable": subscription_remains_billable,
    }

    after = {
        "subscription_status": final_status,
        "inspection_fee_status": final_inspection_fee_status,
        "inspection_item_count": len(final_inspection_items),
        "monthly_service_item_count": len(final_monthly_service_items),
        "other_item_count": len(final_other_items),
    }

    if not verification_passed:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "verification_failed",
            "stage": "final_verification",
            "reason": "The Stripe writes completed, but the final subscription state did not pass verification.",
            "inspection_item_id": inspection_item_id,
            "before": classification_result,
            "after": after,
            "writes": {
                "inspection_item_deleted": True,
                "metadata_updated": True,
            },
            "verification": verification,
            "verification_passed": False,
            "requires_manual_review": True,
        }
    
    # success return
    return {
        "subscription_id": subscription_id,
        "customer_id": classification_result["customer_id"],
        "mode": mode,
        "success": True,
        "action": "inspection_fee_removed",
        "stage": "completed",
        "reason": "The expired inspection-fee item was removed successfully.",
        "inspection_item_id": inspection_item_id,
        "before": classification_result,
        "after": after,
        "writes": {
            "inspection_item_deleted": True,
            "metadata_updated": True,
        },
        "verification": verification,
        "verification_passed": True,
        "requires_manual_review": False,
    }
    
# apply-expired-inspection-fee-one subscription
@main.route("/admin/apply-expired-inspection-fee-one/<subscription_id>", methods=["POST"])
def apply_expired_inspection_fee_one(subscription_id): 
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")

    allowed_modes = {
        "test",
        "live",
    }

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    if mode not in allowed_modes:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    result = apply_expired_inspection_fee_internal(subscription_id, mode)

    return result

@main.route("/admin/create-expired-inspection-fee-test-subscription", methods=["POST"])
def create_expired_inspection_fee_test_subscription():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")

    if confirm != "CREATE_TEST_DATA":
        return {
            "error": (
                "Confirmation required. "
                "Submit confirm=CREATE_TEST_DATA."
            )
        }, 400

    if mode != "test":
        return {
            "error": (
                "This route only supports mode=test."
            )
        }, 400

    is_live_key = current_app.config[
        "STRIPE_SECRET_KEY"
    ].startswith("sk_live_")

    if is_live_key:
        return {
            "error": (
                "Refusing to create test data because "
                "the configured Stripe key is live."
            )
        }, 400

    today = datetime.now(timezone.utc).date()

    contract_start_date = (
        today - relativedelta(years=3, days=1)
    )

    inspection_fee_end_date = (
        today - timedelta(days=1)
    )

    contract_end_date = (
        contract_start_date + relativedelta(years=50)
    )

    # Create a non-Ottawa fake customer.
    # Description must not start with "3".
    customer = stripe.Customer.create(
        name="Inspection Removal Test Customer",
        email="inspection-removal-test@example.com",
        description="TEST CUSTOMER - NOT OTTAWA",
    )

    # Create the monthly-service Product.
    monthly_service_product = stripe.Product.create(
        name="Test Monthly Geothermal Service",
        metadata={
            "increaseable": "true",
        },
    )

    # Create its recurring monthly Price.
    monthly_service_price = stripe.Price.create(
        product=monthly_service_product.id,
        unit_amount=10000,
        currency="cad",
        recurring={
            "interval": "month",
        },
    )

    # Create the inspection-fee Product.
    inspection_fee_product = stripe.Product.create(
        name="Test Monthly Inspection Fee",
    )

    # Create its recurring monthly Price.
    inspection_fee_price = stripe.Price.create(
        product=inspection_fee_product.id,
        unit_amount=2500,
        currency="cad",
        recurring={
            "interval": "month",
        },
    )

    # Create an active subscription without requiring a test card.
    subscription = stripe.Subscription.create(
        customer=customer.id,
        collection_method="send_invoice",
        days_until_due=20,
        items=[
            {
                "price": monthly_service_price.id,
                "quantity": 1,
            },
            {
                "price": inspection_fee_price.id,
                "quantity": 1,
            },
        ],
        metadata={
            "contract_start_date": (
                contract_start_date.isoformat()
            ),
            "contract_end_date": (
                contract_end_date.isoformat()
            ),
            "contract_term_years": "50",
            "inspection_fee_start_date": (
                contract_start_date.isoformat()
            ),
            "inspection_fee_end_date": (
                inspection_fee_end_date.isoformat()
            ),
            "inspection_fee_years": "3",
            "inspection_fee_status": "active",
            "billing_rule_version": "1",
        },
    )

    subscription_items = stripe.SubscriptionItem.list(
        subscription=subscription.id,
        limit=100,
    )

    monthly_service_item_id = None
    inspection_item_id = None

    for item in subscription_items.auto_paging_iter():
        price = stripe_get(item, "price", {}) or {}
        price_id = stripe_get(price, "id")

        if price_id == monthly_service_price.id:
            monthly_service_item_id = stripe_get(
                item,
                "id",
            )

            stripe.SubscriptionItem.modify(
                monthly_service_item_id,
                metadata={
                    "item_type": "monthly_service_fee",
                },
            )

        elif price_id == inspection_fee_price.id:
            inspection_item_id = stripe_get(
                item,
                "id",
            )

            stripe.SubscriptionItem.modify(
                inspection_item_id,
                metadata={
                    "item_type": "inspection_fee",
                },
            )

    final_subscription = stripe.Subscription.retrieve(
        subscription.id,
        expand=["customer"],
    )

    return {
        "status": "test_subscription_created",
        "mode": mode,
        "customer_id": customer.id,
        "subscription_id": subscription.id,
        "subscription_status": stripe_get(
            final_subscription,
            "status",
        ),
        "monthly_service_product_id": (
            monthly_service_product.id
        ),
        "monthly_service_price_id": (
            monthly_service_price.id
        ),
        "monthly_service_item_id": (
            monthly_service_item_id
        ),
        "inspection_fee_product_id": (
            inspection_fee_product.id
        ),
        "inspection_fee_price_id": (
            inspection_fee_price.id
        ),
        "inspection_item_id": inspection_item_id,
        "inspection_fee_end_date": (
            inspection_fee_end_date.isoformat()
        ),
        "inspection_fee_status": "active",
    }

# bulk apply to remove expire inspection fee
@main.route("/admin/apply-expired-inspection-fees-all", methods=["POST"])
def apply_expired_inspection_fees_all():
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY."
        }, 400

    mode = request.form.get("mode")

    if mode not in [
        "test",
        "live",
    ]:
        return {
            "error": "Mode required. Submit mode=test or mode=live."
        }, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {
            "error": "You submitted mode=live, but Stripe key is not live."
        }, 400

    if mode == "test" and is_live_key:
        return {
            "error": "You submitted mode=test, but Stripe key is live."
        }, 400

    # Validate controlled batch size
    max_apply_raw = request.form.get("max_apply")

    if not max_apply_raw:
        return {
            "error": "max_apply is required. For example, submit max_apply=5."
        }, 400

    try:
        max_apply = int(max_apply_raw)

    except ValueError:
        return {
            "error": "max_apply must be a whole number."
        }, 400

    if max_apply < 1:
        return {
            "error": "max_apply must be at least 1."
        }, 400

    # Prevent an accidental absurd value
    if max_apply > 1000:
        return {
            "error": "max_apply cannot be greater than 1000."
        }, 400

    # ---------------------------------------------------------
    # Gather fresh candidates using the exact same classification
    # rules as the preview route, so "what apply will touch" always
    # matches "what preview showed."
    # ---------------------------------------------------------
    run_id = str(uuid.uuid4())

    today = datetime.now(timezone.utc).date()

    billable_statuses = [
        "active",
        "past_due",
        "unpaid",
    ]

    candidates = []

    for status in billable_statuses:
        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100,
            expand=["data.customer"],
        )

        for subscription in subscriptions.auto_paging_iter():
            customer = stripe_get(subscription, "customer")
            customer_description = stripe_get(customer, "description", "")

            candidate = classify_expired_inspection_fee_subscription(
                subscription=subscription,
                customer_description=customer_description,
                today=today,
            )

            if (
                candidate["classification"] == "would_remove_inspection_fee"
                and candidate["safe_to_apply"] is True
            ):
                candidates.append(candidate)

    # ---------------------------------------------------------
    # Apply, respecting max_apply as a batch-size safety limit
    # ---------------------------------------------------------

    results = []

    attempted_count = 0
    success_count = 0
    failed_count = 0
    not_attempted_count = 0

    for candidate in candidates:
        if attempted_count >= max_apply:
            not_attempted_count += 1
            results.append({
                "subscription_id": candidate["subscription_id"],
                "status": "not_attempted",
                "reason": "max_apply limit reached",
            })
            continue

        attempted_count += 1

        try:
            result = apply_expired_inspection_fee_internal(
                candidate["subscription_id"],
                mode,
            )
            results.append(result)

            if result.get("success"):
                success_count += 1
            else:
                failed_count += 1

        except Exception as e:
            failed_count += 1
            results.append({
                "subscription_id": candidate["subscription_id"],
                "success": False,
                "status": "failed",
                "error": str(e),
            })

    return {
        "run_id": run_id,
        "mode": mode,
        "status": "completed",
        "max_apply": max_apply,
        "total_candidates": len(candidates),
        "attempted_count": attempted_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "not_attempted_count": not_attempted_count,
        "results": results,
    }

# create-carry-forward-tax-test-data
@main.route("/admin/create-carry-forward-tax-test-data", methods=["POST"])
def create_carry_forward_tax_test_data():
    """
    Create a complete carry-forward test fixture in Stripe test mode.

    Creates:
    - Test Clock starting 25 days in the past
    - Fake Ontario customer attached to the Test Clock
    - Product and prices with exclusive tax behavior
    - Monthly subscription using Stripe automatic tax
    - Next subscription invoice date set to tomorrow
    - Fully unpaid source invoice:
        pre-tax = 10673 cents
        Stripe-calculated HST = approximately 1387 cents
        total = approximately 12060 cents
    - Advances the Test Clock to the current time

    This route never supports LIVE mode.
    """

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")

    if confirm != "CREATE_TEST_DATA":
        return {
            "error": "Confirmation required. Submit confirm=CREATE_TEST_DATA."
        }, 400

    if mode != "test":
        return {
            "error": "This route only supports mode=test."
        }, 400

    stripe_secret_key = current_app.config["STRIPE_SECRET_KEY"]

    if not stripe_secret_key.startswith("sk_test_"):
        return {
            "error": "Refusing to create test data because the Stripe key is not a test key."
        }, 400

    now_toronto = datetime.now(TORONTO_TZ).replace(microsecond=0)
    now_utc = now_toronto.astimezone(timezone.utc)

    test_clock_start = now_utc - timedelta(days=25)
    next_invoice_time = now_toronto + timedelta(days=1)
    next_invoice_utc = next_invoice_time.astimezone(timezone.utc)

    test_clock = None
    customer = None
    product = None
    recurring_price = None
    source_price = None
    subscription = None
    source_invoice = None
    source_invoice_item = None

    try:
        test_clock = stripe.test_helpers.TestClock.create(
            frozen_time=int(test_clock_start.timestamp()),
            name="Carry Forward Tax Test"
        )

        customer = stripe.Customer.create(
            name="Carry Forward Test Customer",
            email="carry-forward-test@example.com",
            description="TEST CUSTOMER - DO NOT USE",
            address={
                "line1": "100 Queen Street West",
                "city": "Toronto",
                "state": "ON",
                "postal_code": "M5H 2N2",
                "country": "CA",
            },
            test_clock=test_clock.id,
            metadata={
                "test_type": "carry_forward_pre_tax_v1"
            }
        )

        product = stripe.Product.create(
            name="Test Monthly Geothermal Service",
            tax_code="txcd_20030000",
            metadata={
                "test_type": "carry_forward_pre_tax_v1"
            }
        )

        recurring_price = stripe.Price.create(
            product=product.id,
            unit_amount=10673,
            currency="cad",
            recurring={
                "interval": "month"
            },
            tax_behavior="exclusive",
            metadata={
                "test_type": "carry_forward_pre_tax_v1",
                "price_type": "recurring"
            }
        )

        source_price = stripe.Price.create(
            product=product.id,
            unit_amount=10673,
            currency="cad",
            tax_behavior="exclusive",
            metadata={
                "test_type": "carry_forward_pre_tax_v1",
                "price_type": "source_invoice"
            }
        )

        subscription = stripe.Subscription.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=20,
            items=[
                {
                    "price": recurring_price.id,
                    "quantity": 1
                }
            ],
            billing_cycle_anchor=int(next_invoice_utc.timestamp()),
            proration_behavior="none",
            automatic_tax={
                "enabled": True
            },
            metadata={
                "test_type": "carry_forward_pre_tax_v1"
            }
        )

        subscription_items = stripe.SubscriptionItem.list(
            subscription=subscription.id,
            limit=100
        )

        monthly_service_item_id = None

        for item in subscription_items.auto_paging_iter():
            monthly_service_item_id = stripe_get(item, "id")

            stripe.SubscriptionItem.modify(
                monthly_service_item_id,
                metadata={
                    "item_type": "monthly_service_fee",
                    "test_type": "carry_forward_pre_tax_v1"
                }
            )

        source_invoice = stripe.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=1,
            automatic_tax={
                "enabled": True
            },
            auto_advance=False,
            description="Synthetic overdue invoice for carry-forward testing",
            metadata={
                "test_type": "carry_forward_pre_tax_v1",
                "purpose": "source_overdue_invoice"
            }
        )

        source_invoice_item = stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=source_invoice.id,
            pricing={
                "price": source_price.id
            },
            quantity=1,
            description="Test monthly geothermal service",
            metadata={
                "item_type": "monthly_service_fee",
                "test_type": "carry_forward_pre_tax_v1"
            }
        )

        finalized_invoice = stripe.Invoice.finalize_invoice(
            source_invoice.id,
            auto_advance=False
        )

        stripe.test_helpers.TestClock.advance(
            test_clock.id,
            frozen_time=int(now_utc.timestamp())
        )

        final_clock = None
        final_clock_status = None

        for _ in range(60):
            final_clock = stripe.test_helpers.TestClock.retrieve(
                test_clock.id
            )

            final_clock_status = stripe_get(final_clock, "status")

            if final_clock_status == "ready":
                break

            time.sleep(1)

        if final_clock_status != "ready":
            return {
                "status": "partial_failure",
                "reason": "Test Clock did not become ready within 60 seconds.",
                "test_clock_id": test_clock.id,
                "test_clock_status": final_clock_status,
                "customer_id": customer.id,
                "subscription_id": subscription.id,
                "source_invoice_id": finalized_invoice.id,
            }, 500

        final_subscription = stripe.Subscription.retrieve(
            subscription.id
        )

        final_invoice = stripe.Invoice.retrieve(
            finalized_invoice.id
        )

        source_total = stripe_get(final_invoice, "total")

        source_total_excluding_tax = stripe_get(final_invoice, "total_excluding_tax")

        source_amount_remaining = stripe_get(final_invoice, "amount_remaining")

        source_amount_paid = stripe_get(final_invoice, "amount_paid", 0)

        source_status = stripe_get(final_invoice, "status")

        source_due_date_ts = stripe_get(final_invoice, "due_date")

        subscription_items = stripe_get(stripe_get(final_subscription, "items", {}), "data", [])

        current_period_end_ts = None

        if subscription_items:
            current_period_end_ts = stripe_get(subscription_items[0], "current_period_end")

        next_invoice_date = None
        days_until_next_invoice = None

        if current_period_end_ts:
            next_invoice_date = datetime.fromtimestamp(
                current_period_end_ts,
                tz=timezone.utc
            ).astimezone(TORONTO_TZ).date()

            test_clock_today = datetime.fromtimestamp(
                stripe_get(final_clock, "frozen_time"),
                tz=timezone.utc
            ).astimezone(TORONTO_TZ).date()

            days_until_next_invoice = (
                next_invoice_date - test_clock_today
            ).days

        automatic_tax = stripe_get(
            final_invoice,
            "automatic_tax",
            {}
        ) or {}

        total_taxes = stripe_get(
            final_invoice,
            "total_taxes",
            []
        ) or []

        calculated_tax = sum(
            stripe_get(tax, "amount", 0)
            for tax in total_taxes
        )

        return {
            "status": "test_data_created",
            "mode": mode,
            "test_clock_id": test_clock.id,
            "test_clock_status": final_clock_status,
            "customer_id": customer.id,
            "product_id": product.id,
            "recurring_price_id": recurring_price.id,
            "source_price_id": source_price.id,
            "subscription_id": final_subscription.id,
            "subscription_status": stripe_get(
                final_subscription,
                "status"
            ),
            "monthly_service_item_id": monthly_service_item_id,
            "next_invoice_date": (
                next_invoice_date.isoformat()
                if next_invoice_date
                else None
            ),
            "days_until_next_invoice": days_until_next_invoice,
            "source_invoice_id": final_invoice.id,
            "source_invoice_number": stripe_get(
                final_invoice,
                "number"
            ),
            "source_invoice_item_id": source_invoice_item.id,
            "source_invoice_status": source_status,
            "source_due_date_ts": source_due_date_ts,
            "source_amount_paid_cents": source_amount_paid,
            "source_amount_remaining_cents": source_amount_remaining,
            "source_total_excluding_tax_cents": source_total_excluding_tax,
            "source_tax_cents": calculated_tax,
            "source_total_cents": source_total,
            "automatic_tax_enabled": stripe_get(
                automatic_tax,
                "enabled",
                False
            ),
            "automatic_tax_status": stripe_get(
                automatic_tax,
                "status"
            ),
            "verification": {
                "invoice_is_open": source_status == "open",
                "invoice_is_fully_unpaid": (
                    source_amount_paid == 0
                    and source_amount_remaining == source_total
                ),
                "pre_tax_amount_is_10673": (
                    source_total_excluding_tax == 10673
                ),
                "tax_amount_is_1387": calculated_tax == 1387,
                "total_amount_is_12060": source_total == 12060,
                "next_invoice_is_tomorrow": (
                    days_until_next_invoice == 1
                ),
            }
        }

    except stripe.error.StripeError as error:
        return {
            "status": "failed",
            "reason": "Stripe rejected the test-data setup.",
            "test_clock_id": test_clock.id if test_clock else None,
            "customer_id": customer.id if customer else None,
            "subscription_id": subscription.id if subscription else None,
            "source_invoice_id": source_invoice.id if source_invoice else None,
            "error": str(error),
        }, 500

    except Exception as error:
        return {
            "status": "failed",
            "reason": "Unexpected test-data setup failure.",
            "test_clock_id": test_clock.id if test_clock else None,
            "customer_id": customer.id if customer else None,
            "subscription_id": subscription.id if subscription else None,
            "source_invoice_id": source_invoice.id if source_invoice else None,
            "error": str(error),
        }, 500

@main.route("/admin/preview-carry-forward-one/<invoice_id>")
def preview_carry_forward_one(invoice_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    try:
        candidate = get_carry_forward_candidate_by_invoice_id(invoice_id)

        if candidate is None:
            return {
                "status": "not_candidate",
                "invoice_id": invoice_id,
                "reason": "Invoice did not pass the basic candidate filters."
            }, 400

        return {
            "status": "success",
            "candidate": candidate
        }

    except stripe.error.InvalidRequestError as error:
        return {
            "status": "failed",
            "invoice_id": invoice_id,
            "error": str(error)
        }, 400

    except stripe.error.StripeError as error:
        return {
            "status": "failed",
            "invoice_id": invoice_id,
            "error": str(error)
        }, 500

# create-late-fee-test-data
@main.route("/admin/create-late-fee-test-data", methods=["POST"])
def create_late_fee_test_data():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    stripe_key = stripe.api_key or ""

    if not stripe_key.startswith("sk_test_"):
        return {
            "status": "blocked",
            "reason": (
                "This test-data route requires a Stripe test key."
            ),
        }, 400

    now_utc = datetime.now(timezone.utc)
    future_due_date_utc = now_utc + timedelta(days=1)

    # ---------------------------------------------------------
    # 1. Create an isolated Stripe test customer
    # ---------------------------------------------------------
    customer = stripe.Customer.create(
        name="Late Fee Test Customer",
        email="late-fee-test@example.com",
        description="Late fee test customer",
        address={
            "line1": "123 Test Street",
            "city": "Toronto",
            "state": "ON",
            "postal_code": "M5V 1A1",
            "country": "CA",
        },
        metadata={
            "test_type": "late_fee",
            "created_by": "create_late_fee_test_data",
        },
    )

    # ---------------------------------------------------------
    # 2. Create a temporary recurring price
    # ---------------------------------------------------------
    product = stripe.Product.create(
        name="Late Fee Test Monthly Service",
        metadata={
            "test_type": "late_fee",
        },
    )

    price = stripe.Price.create(
        product=product.id,
        currency="cad",
        unit_amount=10000,
        recurring={
            "interval": "month",
        },
        metadata={
            "item_type": "monthly_service_fee",
            "test_type": "late_fee",
        },
    )

    # ---------------------------------------------------------
    # 3. Create an active manual-payment subscription
    # ---------------------------------------------------------
    subscription = stripe.Subscription.create(
        customer=customer.id,
        items=[
            {
                "price": price.id,
            }
        ],
        collection_method="send_invoice",
        days_until_due=30,
        metadata={
            "test_type": "late_fee",
            "billing_rule_version": "late_fee_test_v1",
        },
    )

    # ---------------------------------------------------------
    # 4. Create a test 13% HST rate
    # ---------------------------------------------------------
    tax_rate = stripe.TaxRate.create(
        display_name="HST",
        description="Ontario HST test rate",
        jurisdiction="CA - ON",
        percentage=13.0,
        inclusive=False,
        metadata={
            "test_type": "late_fee",
        },
    )

    # ---------------------------------------------------------
    # 5. Create a separate invoice tied to the subscription
    #    with a due date of yesterday
    # ---------------------------------------------------------
    invoice = stripe.Invoice.create(
        customer=customer.id,
        subscription=subscription.id,
        collection_method="send_invoice",
        due_date=int(future_due_date_utc.timestamp()),
        auto_advance=False,
        description="Overdue invoice for late-fee testing",
        metadata={
            "test_type": "late_fee",
            "force_overdue_for_test": "true",
            "allow_compounding_test": "true",
            "expected_pretax_cents": "10000",
            "expected_tax_cents": "1300",
            "expected_total_cents": "11300",
        },
    )

    # ---------------------------------------------------------
    # 6. Add one taxable $100 monthly-service line
    # ---------------------------------------------------------
    invoice_item = stripe.InvoiceItem.create(
        customer=customer.id,
        invoice=invoice.id,
        subscription=subscription.id,
        amount=10000,
        currency="cad",
        discountable=False,
        tax_rates=[tax_rate.id],
        description="Test monthly service fee",
        metadata={
            "item_type": "monthly_service_fee",
            "test_type": "late_fee",
        },
    )

    # ---------------------------------------------------------
    # 7. Finalize the invoice so it becomes open
    # ---------------------------------------------------------
    finalized_invoice = stripe.Invoice.finalize_invoice(
        invoice.id,
        auto_advance=False,
    )

    return {
        "status": "success",
        "customer_id": customer.id,
        "product_id": product.id,
        "price_id": price.id,
        "subscription_id": subscription.id,
        "tax_rate_id": tax_rate.id,
        "invoice_id": finalized_invoice.id,
        "invoice_number": stripe_get(
            finalized_invoice,
            "number"
        ),
        "invoice_status": stripe_get(
            finalized_invoice,
            "status"
        ),
        "collection_method": stripe_get(
            finalized_invoice,
            "collection_method"
        ),
        "due_date": stripe_get(
            finalized_invoice,
            "due_date"
        ),
        "total_excluding_tax": stripe_get(
            finalized_invoice,
            "total_excluding_tax"
        ),
        "tax_cents": (
            stripe_get(finalized_invoice, "total", 0)
            - stripe_get(
                finalized_invoice,
                "total_excluding_tax",
                0
            )
        ),
        "total": stripe_get(
            finalized_invoice,
            "total"
        ),
        "amount_remaining": stripe_get(
            finalized_invoice,
            "amount_remaining"
        ),
        "invoice_item_id": invoice_item.id,
        "expected_late_fee_base_cents": 10000,
        "expected_first_late_fee_cents": 150,
    }

# enable-late-fee-compounding-test
@main.route("/admin/enable-late-fee-compounding-test/<invoice_id>", methods=["POST"])
def enable_late_fee_compounding_test(invoice_id):
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    stripe_key = stripe.api_key or ""

    if not stripe_key.startswith("sk_test_"):
        return {
            "status": "blocked",
            "reason": "This route requires a Stripe test key.",
        }, 400

    invoice = stripe.Invoice.modify(
        invoice_id,
        metadata={
            "force_overdue_for_test": "true",
            "allow_compounding_test": "true",
        },
    )

    invoice_metadata = stripe_get(invoice, "metadata", {})

    return {
    "status": "success",
    "invoice_id": invoice.id,
    "force_overdue_for_test": stripe_get(invoice_metadata, "force_overdue_for_test"),
    "allow_compounding_test": stripe_get(invoice_metadata, "allow_compounding_test"),
}

# ------------------------------------------------------customize email sender
FIRST_REMINDER_DAY = 1
SECOND_REMINDER_DAY = 14
THIRD_REMINDER_DAY = 28
FINAL_NOTICE_DAY = 42

# preview payment reminders
@main.route("/admin/preview-payment-reminders", methods=["GET"])
def preview_payment_reminders():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    invoices = stripe.Invoice.list(
        status="open",
        limit=100,
        expand=["data.customer"]
    )

    today = datetime.now(timezone.utc).date()

    results = []

    summary= {
        "checked": 0,
        "eligible": 0,
        "not_eligible": 0,

        "first_reminder": 0,
        "second_reminder": 0,
        "third_reminder": 0,
        "final_notice": 0,

        "not_overdue": 0,
        "missing_customer_email":0,
        "no_amount_remaining":0,
    }

    for invoice in invoices.auto_paging_iter():

        # Retrieve invoice information
        invoice_id = stripe_get(invoice, "id")
        invoice_number = stripe_get(invoice, "number")
        hosted_invoice_url = stripe_get(invoice, "hosted_invoice_url")

        customer = stripe_get(invoice, "customer")
        customer_id = stripe_get(customer, "id")
        collection_method = stripe_get(invoice, "collection_method")
        status = stripe_get(invoice, "status")
        due_date_ts = stripe_get(invoice, "due_date")
        amount_remaining_cents = stripe_get(invoice, "amount_remaining") or 0
        amount_remaining = cents_to_money(amount_remaining_cents) if amount_remaining_cents else None
        created_at_ts = stripe_get(invoice, "created")
        created_at_dt = stripe_timestamp_to_utc_datetime(created_at_ts)

        customer_email = stripe_get(customer, "email")
        customer_name =stripe_get(customer, "name")

        eligible = None
        skip_reason = None
        reminder_stage = None

        summary["checked"] += 1

        if due_date_ts:
            due_date_dt = stripe_timestamp_to_utc_datetime(due_date_ts)
            stripe_due_date = due_date_dt.date()
            effective_due_date = stripe_due_date

        else:
            stripe_due_date = None
            effective_due_date = (created_at_dt + relativedelta(days=20)).date()

        days_overdue = (today - effective_due_date).days

        # Determine eligibility
        if amount_remaining_cents <= 0:
            eligible = False
            skip_reason = "no_amount_remaining"
            # should seperate business logic from reporting
            # summary["no_amount_remaining"] += 1
        
        elif days_overdue <= 0:
            eligible = False
            skip_reason = "not_overdue"
            # should seperate business logic from reporting
            # summary["not_overdue"] += 1

        elif not customer_email:
            eligible = False
            skip_reason = "missing_customer_email"
            # should seperate business logic from reporting
            # summary["missing_customer_email"] += 1

        else:
            eligible = True
            skip_reason = None

        # Update summary statistics
        if eligible:
            summary["eligible"] += 1
        else:
            summary["not_eligible"] += 1

        if skip_reason:
            summary[skip_reason] += 1

        if eligible:
            if days_overdue >= FINAL_NOTICE_DAY:
                reminder_stage = "final_notice"
                # should seperate business logic from reporting
                # summary["final_notice"] += 1

            elif days_overdue >= THIRD_REMINDER_DAY:
                reminder_stage = "third_reminder"
                # should seperate business logic from reporting
                # summary["third_reminder"] += 1

            elif days_overdue >= SECOND_REMINDER_DAY:
                reminder_stage = "second_reminder"
                # should seperate business logic from reporting
                # summary["second_reminder"] += 1

            else:
                reminder_stage = "first_reminder"
                # should seperate business logic from reporting
                # summary["first_reminder"] += 1

        if reminder_stage:
            summary[reminder_stage] += 1

        # Build preview result
        result = {
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "hosted_invoice_url": hosted_invoice_url if hosted_invoice_url else None,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "collection_method": collection_method,
            "status": status,
            "stripe_due_date": stripe_due_date.isoformat() if stripe_due_date else None,
            "effective_due_date": effective_due_date.isoformat(),
            "days_overdue": days_overdue,
            "amount_remaining_cents": amount_remaining_cents,
            "amount_remaining": amount_remaining,
            "reminder_stage": reminder_stage,
            "eligible": eligible,
            "skip_reason": skip_reason,
        }
    
        results.append(result)

    # Sort invoices by overdue days descending so the most overdue invoices appear first
    # given a result, return its days overdue
    # result → result["days_overdue"]
    # lambda result:
    #     take this result
    #     return result["days_overdue"]

    # For each item, calculate something
    # item -> calculated value
    sorted_list = sorted(results, key=lambda result: result["days_overdue"], reverse=True)
    # For every item in the results list, temporarily call it result, then use that result's days_overdue value for sorting

    return {
        "results": sorted_list,
        "summary": summary,
    }

# run it once for investigation, not as part of normal reminder workflow cuz paid could potentially contain thousands of invoices
@main.route("/admin/debug-invoice-status-counts", methods=["GET"])
def debug_invoice_status_counts():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    statuses = [
        "draft",
        "open",
        "paid",
        "void",
        "uncollectible",
    ]

    counts = {}

    for status in statuses:
        invoices = stripe.Invoice.list(
            status=status,
            limit=100,
        )

        count = 0

        for invoice in invoices.auto_paging_iter():
            count += 1

        counts[status] = count

    return {
        "invoice_status_counts": counts,
        "total_invoices": sum(counts.values()),
    }

# ------------------------------------autopay testing--------------------------------------------------
@main.route("/admin/create-autopay-checkout-session", methods=["POST"])
def create_autopay_checkout_session():
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    customer_id = request.form.get("customer_id")
    monthly_price_id = request.form.get("monthly_price_id")
    inspection_price_id = request.form.get("inspection_price_id")

    if not monthly_price_id:
        return {"error": "monthly_price_id is required"}, 400

    if not inspection_price_id:
            return {"error": "inspection_price_id is required"}, 400

    session_params = {
        # Tells Stripe: "This checkout page should set up a recurring subscription, not a one-time purchase."
        "mode": "subscription",
        "line_items": [
            {
                "price": monthly_price_id,
                "quantity": 1,
            },
            {
                "price": inspection_price_id,
                "quantity": 1,
            }
        ],
        # Tells Stripe: "Always ask the customer for a payment method during checkout"
        "payment_method_collection": "always",
        "success_url": "http://localhost:5000/admin/checkout-success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": "http://localhost:5000/admin/checkout-cancel",
        "automatic_tax":{
            "enabled": True,
        },
        # Remember which price is which, so checkout_success can tag subscription items correctly without guessing
        "metadata": {
            "monthly_price_id": monthly_price_id,
            "inspection_price_id": inspection_price_id,
        },
    }

    if customer_id:
        session_params["customer"] = customer_id
        session_params["customer_update"] = {
            "address": "auto"
        }

    checkout_session = stripe.checkout.Session.create(**session_params)

    return render_template("autopay_link_ready.html", checkout_url=checkout_session.url)

@main.route("/admin/checkout-success")
def checkout_success():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # request.args is Flask's way of reading whatever came after the ? in the URL — the query parameters
    session_id = request.args.get("session_id")

    if not session_id:
        return {"error": "Missing session_id"}, 400

    checkout_session = stripe.checkout.Session.retrieve(session_id)

    today= datetime.now(timezone.utc).date()
    expected_inspection_end_dt = today + relativedelta(years=3)
    expected_contract_end_dt = today + relativedelta(years=50)

    contract_start_dt = today
    inspection_start_dt = today

    metadata_to_merge = {
        "contract_start_date": contract_start_dt.isoformat(),
        "contract_end_date": expected_contract_end_dt.isoformat(),
        "contract_term_years": "50",
        "inspection_fee_start_date": inspection_start_dt.isoformat(),
        "inspection_fee_end_date": expected_inspection_end_dt.isoformat(),
        "inspection_fee_years": "3",
        "inspection_fee_status": "active",
        "billing_rule_version": "1",
    }

    stripe.Subscription.modify(
        checkout_session.subscription, 
        metadata=metadata_to_merge,
    )

    # -----------------------------------------------------------
    # Tag each subscription item using the price->type mapping we stored on the Checkout Session at creation time. 
    # This works correctly regardless of which Products/Prices were used, with no hardcoded IDs and no metadata guessing.
    # -----------------------------------------------------------
    session_metadata= stripe_get(checkout_session, "metadata", {}) or {}

    known_monthly_price_id = stripe_get(session_metadata, "monthly_price_id")
    known_inspection_price_id = stripe_get(session_metadata, "inspection_price_id")

    subscription_items = stripe.SubscriptionItem.list(
        subscription=checkout_session.subscription,
        limit=100,
    )

    item_tagging_results = []

    for item in subscription_items.auto_paging_iter():
        subscription_item_id = stripe_get(item, "id")

        price = stripe_get(item, "price", {}) or {}
        price_id = stripe_get(price, "id")

        if price_id == known_monthly_price_id:
            intended_item_type = "monthly_service_fee"
        elif price_id == known_inspection_price_id:
            intended_item_type = "inspection_fee"
        else: 
            intended_item_type = None

        if intended_item_type is None:
            item_tagging_results.append({
                "subscription_item_id": subscription_item_id,
                "price_id": price_id,
                "status": "unrecognized_price_not_tagged",
            })
            continue

        try:
            stripe.SubscriptionItem.modify(
                subscription_item_id,
                metadata={
                    "item_type": intended_item_type,
                },
            )

            item_tagging_results.append({
                "subscription_item_id": subscription_item_id,
                "price_id": price_id,
                "item_type": intended_item_type,
                "status": "tagged",
            })

        except Exception as e:
            item_tagging_results.append({
                "subscription_item_id": subscription_item_id,
                "price_id": price_id,
                "status": "tagging_failed",
                "error":str(e),
            })

    return {
        "status": "checkout_complete",
        "checkout_session_id": checkout_session.id,
        "customer_id": checkout_session.customer,
        "subscription_id": checkout_session.subscription,
        "payment_status": checkout_session.payment_status,
        "item_tagging_results": item_tagging_results,
    }

# autopay html
@main.route("/admin/autopay-setup")
def autopay_setup():
    if not logged_in_or_dev():
        return redirect("/login")

    return render_template("autopay_setup.html")

# -------------------------------------------------------50 year contract end---------------------------
# Classification of contract end (50-year mark)
def classify_contract_end_subscription(subscription, today):
    """
    Read-only classifier. Scans subscription metadata for contract_end_date
    and determines whether the 50-year contract term has been reached.

    Does NOT modify Stripe. Mirrors classify_expired_inspection_fee_subscription.
    """
    billable_statuses = {
        "active",
        "past_due",
        "unpaid"
    }

    metadata = stripe_metadata_to_dict(stripe_get(subscription, "metadata", {}) or {})

    contract_end_date_raw = metadata.get("contract_end_date")
    contract_start_date_raw = metadata.get("contract_start_date")
    contract_term_years = metadata.get("contract_term_years")

    status = stripe_get(subscription, "status")
    subscription_id = stripe_get(subscription, "id")

    customer = stripe_get(subscription, "customer")

    if isinstance(customer, str):
        customer_id = customer
    else:
        customer_id = stripe_get(customer, "id")

    schedule = stripe_get(subscription, "schedule")
    cancel_at = stripe_get(subscription, "cancel_at")
    cancel_at_period_end = stripe_get(subscription, "cancel_at_period_end")

    if schedule is None or isinstance(schedule, str):
        schedule_id = schedule
    else:
        schedule_id = stripe_get(schedule, "id")

    # count monthly service items (same item_type convention you already use)
    monthly_service_items = []
    other_items = []

    subscription_items = stripe_get(subscription, "items", {}) or {}
    item_data = stripe_get(subscription_items, "data", []) or []

    for item in item_data:
        item_metadata = stripe_metadata_to_dict(stripe_get(item, "metadata", {}) or {})
        item_type = item_metadata.get("item_type")

        if item_type == "monthly_service_fee":
            monthly_service_items.append(item)
        else:
            other_items.append(item)

    result = {
        "subscription_id": subscription_id,
        "customer_id": customer_id,
        "subscription_status": status,
        "contract_start_date": contract_start_date_raw,
        "contract_end_date": contract_end_date_raw,
        "contract_term_years": contract_term_years,
        "monthly_service_item_count": len(monthly_service_items),
        "other_item_count": len(other_items),
        "classification": None,
        "safe_to_apply": False,
        "reason": None,
        "schedule_id": schedule_id,
        "cancel_at": cancel_at,
        "cancel_at_period_end": cancel_at_period_end,
        "monthly_service_item_id": None,
    }

    if status not in billable_statuses:
        result["classification"] = "manual_review"
        result["reason"] = f"Subscription status is not billable: {status}"
        return result

    if schedule_id:
        result["classification"] = "manual_review"
        result["reason"] = f"Attached schedule exists: {schedule_id}"
        return result

    if cancel_at is not None or cancel_at_period_end is True:
        result["classification"] = "manual_review"
        result["reason"] = "Subscription already has a Stripe-side end mechanism set."
        return result

    if not contract_end_date_raw:
        result["classification"] = "manual_review"
        result["reason"] = "contract_end_date is missing."
        return result

    try:
        contract_end_date = date.fromisoformat(contract_end_date_raw)
    except (TypeError, ValueError):
        result["classification"] = "manual_review"
        result["reason"] = f"Invalid contract_end_date: {contract_end_date_raw}"
        return result

    result["contract_end_date"] = contract_end_date.isoformat()

    if today < contract_end_date:
        result["classification"] = "not_due_yet"
        result["reason"] = f"Contract does not end until {contract_end_date.isoformat()}"
        return result

    # Past contract_end_date — now validate item shape before flagging safe
    if len(monthly_service_items) != 1:
        result["classification"] = "manual_review"
        result["reason"] = f"Expected exactly 1 monthly service item, but found {len(monthly_service_items)}."
        return result

    if len(other_items) != 0:
        result["classification"] = "manual_review"
        result["reason"] = f"Expected 0 unrecognized subscription items, but found {len(other_items)}."
        return result

    monthly_service_item = monthly_service_items[0]
    result["monthly_service_item_id"] = stripe_get(monthly_service_item, "id")

    result["classification"] = "would_end_contract"
    result["safe_to_apply"] = True
    result["reason"] = "Contract has reached its 50-year end date and is eligible for termination."

    return result


# Preview route — read-only, scans metadata across all billable subscriptions
@main.route("/admin/preview-contract-end-50yr")
def preview_contract_end_50yr():
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    target_subscription_id = request.args.get("subscription_id")
    today = datetime.now(timezone.utc).date()

    billable_statuses = ["active", "past_due", "unpaid"]

    results = []

    classification_counts = {
        "would_end_contract": 0,
        "not_due_yet": 0,
        "manual_review": 0,
    }

    if target_subscription_id:
        subscription = stripe.Subscription.retrieve(target_subscription_id)

        result = classify_contract_end_subscription(
            subscription=subscription,
            today=today,
        )

        results.append(result)

        classification = result["classification"]
        classification_counts[classification] = classification_counts.get(classification, 0) + 1

    else:
        for status in billable_statuses:
            subscriptions = stripe.Subscription.list(
                status=status,
                limit=100,
            )

            for subscription in subscriptions.auto_paging_iter():
                result = classify_contract_end_subscription(
                    subscription=subscription,
                    today=today,
                )

                results.append(result)

                classification = result["classification"]
                classification_counts[classification] = classification_counts.get(classification, 0) + 1

    safe_to_apply_count = sum(1 for result in results if result["safe_to_apply"] is True)

    return {
        "read_only": True,
        "target_subscription_id": target_subscription_id,
        "today": today.isoformat(),
        "checked_count": len(results),
        "safe_to_apply_count": safe_to_apply_count,
        "classification_counts": classification_counts,
        "results": results,
    }

# apply_contract_end_50yr_internal
def apply_contract_end_50yr_internal(subscription_id, mode):
    """
    Cancel a subscription that has reached its 50-year contract_end_date.

    Ownership transfers to the client at this point — we stop billing
    entirely. This cancels the whole Stripe subscription (not just an item).
    """
    allowed_modes = {"test", "live"}

    if not subscription_id:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "input_validation",
            "reason": "Subscription ID is required.",
        }

    if mode not in allowed_modes:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "input_validation",
            "reason": f"Unsupported mode: {mode!r}",
        }

    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "subscription_retrieval",
            "reason": str(exc),
        }

    today = datetime.now(timezone.utc).date()

    classification_result = classify_contract_end_subscription(
        subscription=subscription,
        today=today,
    )

    if (
        classification_result["classification"] != "would_end_contract"
        or classification_result["safe_to_apply"] is not True
    ):
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "pre_write_validation",
            "reason": classification_result["reason"],
            "classification": classification_result["classification"],
            "safe_to_apply": classification_result["safe_to_apply"],
            "before": classification_result,
        }

    # -----------------------------------------------------------
    # Cancel the subscription
    # -----------------------------------------------------------
    try:
        canceled_subscription = stripe.Subscription.cancel(
            subscription_id,
            prorate=False,
        )

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "subscription_cancellation",
            "before": classification_result,
            "reason": str(exc),
            "writes": {
                "subscription_canceled": False,
                "metadata_updated": False,
            },
        }

    canceled_status = stripe_get(canceled_subscription, "status")

    if canceled_status != "canceled":
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "not_applied",
            "stage": "subscription_cancellation",
            "reason": f"Stripe did not confirm cancellation. Status: {canceled_status!r}",
            "before": classification_result,
            "writes": {
                "subscription_canceled": False,
                "metadata_updated": False,
            },
        }

    # -----------------------------------------------------------
    # Update metadata to record that contract has ended
    # -----------------------------------------------------------
    try:
        stripe.Subscription.modify(
            subscription_id,
            metadata={
                "contract_status": "ended",
                "contract_end_reason": "50_year_term_completed_ownership_transferred",
            },
        )

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "partial_failure",
            "stage": "metadata_update",
            "reason": str(exc),
            "before": classification_result,
            "writes": {
                "subscription_canceled": True,
                "metadata_updated": False,
            },
            "requires_metadata_repair": True,
        }

    # -----------------------------------------------------------
    # Retrieve and verify final state
    # -----------------------------------------------------------
    try:
        final_subscription = stripe.Subscription.retrieve(subscription_id)

    except stripe.error.StripeError as exc:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "verification_failed",
            "stage": "post_write_retrieval",
            "reason": str(exc),
            "before": classification_result,
            "writes": {
                "subscription_canceled": True,
                "metadata_updated": True,
            },
            "requires_manual_review": True,
        }

    final_status = stripe_get(final_subscription, "status")
    final_metadata = stripe_metadata_to_dict(stripe_get(final_subscription, "metadata", {}) or {})
    final_contract_status = final_metadata.get("contract_status")

    subscription_is_canceled = final_status == "canceled"
    contract_status_is_ended = final_contract_status == "ended"

    verification_passed = all([
        subscription_is_canceled,
        contract_status_is_ended,
    ])

    verification = {
        "subscription_is_canceled": subscription_is_canceled,
        "contract_status_is_ended": contract_status_is_ended,
    }

    after = {
        "subscription_status": final_status,
        "contract_status": final_contract_status,
    }

    if not verification_passed:
        return {
            "subscription_id": subscription_id,
            "customer_id": classification_result["customer_id"],
            "mode": mode,
            "success": False,
            "action": "verification_failed",
            "stage": "final_verification",
            "reason": "Cancellation completed, but final state did not pass verification.",
            "before": classification_result,
            "after": after,
            "writes": {
                "subscription_canceled": True,
                "metadata_updated": True,
            },
            "verification": verification,
            "verification_passed": False,
            "requires_manual_review": True,
        }

    return {
        "subscription_id": subscription_id,
        "customer_id": classification_result["customer_id"],
        "mode": mode,
        "success": True,
        "action": "contract_ended_subscription_canceled",
        "stage": "completed",
        "reason": "50-year contract term completed. Subscription canceled, ownership transferred to client.",
        "before": classification_result,
        "after": after,
        "writes": {
            "subscription_canceled": True,
            "metadata_updated": True,
        },
        "verification": verification,
        "verification_passed": True,
        "requires_manual_review": False,
    }


# apply-contract-end-50yr-one
@main.route("/admin/apply-contract-end-50yr-one/<subscription_id>", methods=["POST"])
def apply_contract_end_50yr_one(subscription_id):
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")

    allowed_modes = {"test", "live"}

    if confirm != "APPLY":
        return {"error": "Confirmation required. Submit confirm=APPLY."}, 400

    if mode not in allowed_modes:
        return {"error": "Mode required. Submit mode=test or mode=live."}, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {"error": "You submitted mode=live, but Stripe key is not live."}, 400

    if mode == "test" and is_live_key:
        return {"error": "You submitted mode=test, but Stripe key is live."}, 400

    result = apply_contract_end_50yr_internal(subscription_id, mode)

    return result


# bulk apply
@main.route("/admin/apply-contract-end-50yr-all", methods=["POST"])
def apply_contract_end_50yr_all():
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {"error": "Confirmation required. Submit confirm=APPLY."}, 400

    mode = request.form.get("mode")

    if mode not in ["test", "live"]:
        return {"error": "Mode required. Submit mode=test or mode=live."}, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if mode == "live" and not is_live_key:
        return {"error": "You submitted mode=live, but Stripe key is not live."}, 400

    if mode == "test" and is_live_key:
        return {"error": "You submitted mode=test, but Stripe key is live."}, 400

    max_apply_raw = request.form.get("max_apply")

    if not max_apply_raw:
        return {"error": "max_apply is required. For example, submit max_apply=5."}, 400

    try:
        max_apply = int(max_apply_raw)
    except ValueError:
        return {"error": "max_apply must be a whole number."}, 400

    if max_apply < 1 or max_apply > 1000:
        return {"error": "max_apply must be between 1 and 1000."}, 400

    run_id = str(uuid.uuid4())
    today = datetime.now(timezone.utc).date()

    billable_statuses = ["active", "past_due", "unpaid"]

    candidates = []

    for status in billable_statuses:
        subscriptions = stripe.Subscription.list(
            status=status,
            limit=100,
        )

        for subscription in subscriptions.auto_paging_iter():
            candidate = classify_contract_end_subscription(
                subscription=subscription,
                today=today,
            )

            if (
                candidate["classification"] == "would_end_contract"
                and candidate["safe_to_apply"] is True
            ):
                candidates.append(candidate)

    results = []
    attempted_count = 0
    success_count = 0
    failed_count = 0
    not_attempted_count = 0

    for candidate in candidates:
        if attempted_count >= max_apply:
            not_attempted_count += 1
            results.append({
                "subscription_id": candidate["subscription_id"],
                "status": "not_attempted",
                "reason": "max_apply limit reached",
            })
            continue

        attempted_count += 1

        try:
            result = apply_contract_end_50yr_internal(
                candidate["subscription_id"],
                mode,
            )
            results.append(result)

            if result.get("success"):
                success_count += 1
            else:
                failed_count += 1

        except Exception as e:
            failed_count += 1
            results.append({
                "subscription_id": candidate["subscription_id"],
                "success": False,
                "status": "failed",
                "error": str(e),
            })

    return {
        "run_id": run_id,
        "mode": mode,
        "status": "completed",
        "max_apply": max_apply,
        "total_candidates": len(candidates),
        "attempted_count": attempted_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "not_attempted_count": not_attempted_count,
        "results": results,
    }

# fake subscription that end date is already in the past so i can test
@main.route("/admin/create-contract-end-50yr-test-subscription", methods=["POST"])
def create_contract_end_50yr_test_subscription():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    confirm = request.form.get("confirm")
    mode = request.form.get("mode")

    if confirm != "CREATE_TEST_DATA":
        return {"error": "Confirmation required. Submit confirm=CREATE_TEST_DATA."}, 400

    if mode != "test":
        return {"error": "This route only supports mode=test."}, 400

    is_live_key = current_app.config["STRIPE_SECRET_KEY"].startswith("sk_live_")

    if is_live_key:
        return {"error": "Refusing to create test data because the configured Stripe key is live."}, 400

    today = datetime.now(timezone.utc).date()

    # contract "started" 50 years + 1 day ago, so it's already past end date
    contract_start_date = today - relativedelta(years=50, days=1)
    contract_end_date = today - timedelta(days=1)

    customer = stripe.Customer.create(
        name="Contract End Test Customer",
        email="contract-end-test@example.com",
        description="TEST CUSTOMER - 50 YEAR CONTRACT",
    )

    product = stripe.Product.create(
        name="Test Monthly Geothermal Service (Contract End)",
        metadata={"increaseable": "true"},
    )

    price = stripe.Price.create(
        product=product.id,
        unit_amount=10000,
        currency="cad",
        recurring={"interval": "month"},
    )

    subscription = stripe.Subscription.create(
        customer=customer.id,
        collection_method="send_invoice",
        days_until_due=20,
        items=[{"price": price.id, "quantity": 1}],
        metadata={
            "contract_start_date": contract_start_date.isoformat(),
            "contract_end_date": contract_end_date.isoformat(),
            "contract_term_years": "50",
        },
    )

    subscription_items = stripe.SubscriptionItem.list(subscription=subscription.id, limit=100)

    monthly_service_item_id = None

    for item in subscription_items.auto_paging_iter():
        monthly_service_item_id = stripe_get(item, "id")

        stripe.SubscriptionItem.modify(
            monthly_service_item_id,
            metadata={"item_type": "monthly_service_fee"},
        )

    return {
        "status": "test_subscription_created",
        "customer_id": customer.id,
        "subscription_id": subscription.id,
        "monthly_service_item_id": monthly_service_item_id,
        "contract_end_date": contract_end_date.isoformat(),
    }

# -------------------------------autopay with prefilled customer info-----------------
@main.route("/admin/create-autopay-customer", methods=["POST"])
def create_autopay_customer():
    if not logged_in_or_dev():
        return redirect("/login")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    name= request.form.get("name")
    email= request.form.get("email")
    phone= request.form.get("phone")
    language= request.form.get("language")
    invoice_prefix= request.form.get("invoice_prefix")
    description= request.form.get("description")

    if invoice_prefix:
        invoice_prefix = invoice_prefix.upper()

    if not name:
        return {"error": "name is required. "}, 400

    if not email:
            return {"error": "email is required. "}, 400

    if not invoice_prefix:
            return {"error": "invoice_prefix is required. "}, 400

    address_line1= request.form.get("address_line1")
    address_city= request.form.get("address_city")
    address_state= request.form.get("address_state")
    address_postal_code= request.form.get("address_postal_code")
    address_country= request.form.get("address_country")

    address = {
        "line1": address_line1, 
        "city": address_city, 
        "state": address_state, 
        "postal_code": address_postal_code, 
        "country": address_country,
    }

    try:
        customer = stripe.Customer.create(
            name=name,
            email=email,
            description=description,
            address=address,
            phone=phone,
            preferred_locales=[language],
            invoice_prefix=invoice_prefix,
        )

    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
        }, 400

    return {
        "status": "customer_created",
        "customer_id": customer.id,
        "name": customer.name,
        "email": customer.email,
    }

@main.route("/admin/create-autopay-customer-page")
def create_autopay_customer_page():
    if not logged_in_or_dev():
        return redirect("/login")

    return render_template("create_autopay_customer.html")

# carry_forward_dashboard
def get_carry_forward_dashboard_data():
    audit_result = find_carry_forward_candidates()

    # Total dollar amount currently eligible to carry forward
    total_eligible_amount = sum(
        candidate["proposed_carry_forward_amount"] or 0
        for candidate in audit_result["candidates"]
        if candidate["eligible_to_apply"]
    )

    run_logs = []

    latest_log = CarryForwardLog.query.order_by(
        CarryForwardLog.created_at.desc()
    ).first()

    if latest_log:
        run_id = latest_log.run_id

        run_logs = CarryForwardLog.query.filter_by(
            run_id=run_id
        ).all()

    total_amount_cents = 0

    for log in run_logs:
        if log.status == "success":
            total_amount_cents += log.amount_cents or 0

    last_run_time = None
    last_run_time_toronto = None
    ran_today = False

    success_count = sum(1 for r in run_logs if r.status == "success")
    failed_count = sum(1 for r in run_logs if r.status == "failed")
    skipped_count = sum(1 for r in run_logs if r.status == "skipped")

    if latest_log:
        last_run_time = latest_log.created_at

        if last_run_time.tzinfo is None:
            last_run_time = last_run_time.replace(tzinfo=timezone.utc)

        last_run_time_toronto = last_run_time.astimezone(TORONTO_TZ)

        now_toronto = datetime.now(TORONTO_TZ).date()

        if last_run_time_toronto.date() == now_toronto:
            ran_today = True
        else:
            ran_today = False

    recent_logs = []

    for log in run_logs:
        created_at = log.created_at

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        created_at_toronto = created_at.astimezone(TORONTO_TZ)

        recent_logs.append({
            "created_at": created_at_toronto.strftime("%Y-%m-%d %I:%M %p"),
            "status": log.status,
            "invoice_id": log.invoice_id,
            "invoice_number": log.source_invoice_number or log.invoice_id,
            "invoice_item_id": log.invoice_item_id,
            "amount": f"${cents_to_money(log.amount_cents):.2f}",
            "reason_or_error": log.reason or log.error or "-"
        })

    return {
        "audit": audit_result,
        "total_eligible_amount": f"${total_eligible_amount:.2f}",
        "today_status": {
            "ran_today": ran_today,
            "last_run_time": last_run_time_toronto.strftime("%Y-%m-%d %I:%M %p") if last_run_time_toronto else None,
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "total_amount": f"${cents_to_money(total_amount_cents):.2f}",
        },
        "recent_logs": recent_logs,
    }

@main.route("/admin/carry-forward-dashboard")
def carry_forward_dashboard():
    if not session.get("logged_in"):
        return redirect("/login")

    data = get_carry_forward_dashboard_data()

    return render_template(
        "carry_forward_dashboard.html",
        data=data
    )