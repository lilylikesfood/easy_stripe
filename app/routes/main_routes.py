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

main = Blueprint("main", __name__)


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

    def stripe_get(obj, key, default=None):
        if obj is None:
            return default
        if key in obj:
            return obj[key]
        return default

    def cents_to_money(cents):
        return round((cents or 0) / 100, 2)

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

        raw_age_days = (now - effective_due_date).days

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

def find_late_fee_candidates():
    stripe.api_key= current_app.config["STRIPE_SECRET_KEY"]

    now= datetime.now(timezone.utc)
    late_fee_month= now.strftime("%Y-%m")
    # June 2026 → "2026-06"
    
    def stripe_get(obj, key, default=None):
        if obj is None:
            return default
        if key in obj:
            return obj[key]
        return default
    
    def cents_to_money(cents):
        return round((cents or 0) / 100, 2)
    
    def money_to_cents(amount):
        return int(round(amount * 100))
    
    def calculate_late_fee_cents(amount_remaining_cents):
        return int(round(amount_remaining_cents * 0.015))
    
    def late_fee_already_exists(customer_id, source_invoice_id, late_fee_month):
        invoice_items= stripe.InvoiceItem.list(
            customer=customer_id,
            limit=100
        )

        for item in invoice_items.auto_paging_iter():
            metadata= stripe_get(item, "metadata", {})

            if (
                stripe_get(metadata, "type") == "late_fee"
                and stripe_get(metadata, "source_invoice_id") == source_invoice_id
                and stripe_get(metadata, "late_fee_month") == late_fee_month
            ):
                return True
            
        return False
    
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
        subscription= stripe_get(invoice, "subscription")

        if isinstance(customer, str):
            customer_id = customer
            customer_name= None
            customer_email = None
        else:
            customer_id= stripe_get(customer, "id")
            customer_name= stripe_get(customer, "name")
            customer_email= stripe_get(customer, "email")

        if isinstance(subscription, str):
            subscription_id = subscription
            next_invoice_date = None
        elif subscription:
            subscription_id = stripe_get(subscription, "id")
            current_period_end_ts = stripe_get(subscription, "current_period_end")

            if current_period_end_ts:
                next_invoice_date = datetime.fromtimestamp(
                    current_period_end_ts,
                    tz=timezone.utc
                ).date().isoformat()
            else:
                next_invoice_date = None
        else:
            subscription_id = None
            next_invoice_date = None

        due_date_ts= stripe_get(invoice, "due_date")
        created_ts= stripe_get(invoice, "created")

        if due_date_ts:
            effective_due_date= datetime.fromtimestamp(due_date_ts, tz=timezone.utc)

        else: 
            created_date= datetime.fromtimestamp(created_ts, tz= timezone.utc)
            effective_due_date= created_date + timedelta(days=20)

        raw_days= (now - effective_due_date).days
        # for testing
        # raw_days = 10

        if raw_days <= 0:
            continue

        days_overdue= raw_days

        late_fee_cents = calculate_late_fee_cents(amount_remaining)

        invoice_id= stripe_get(invoice, "id")

        already_applied= late_fee_already_exists(
            customer_id,
            invoice_id,
            late_fee_month
        )

        if not already_applied:
            total_late_fee_cents += late_fee_cents

        late_fee_candidates.append({
            "customer_id": customer_id,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "invoice_id": invoice_id,
            "collection_method": stripe_get(invoice, "collection_method"),
            "amount_remaining": cents_to_money(amount_remaining),
            "effective_due_date": effective_due_date.date().isoformat(),
            "days_overdue": days_overdue,
            "late_fee_month": late_fee_month,
            "eligible_to_apply": not already_applied,
            "skip_reason": "already applied this month" if already_applied else None,
            "late_fee_rate": "1.5%",
            "late_fee": cents_to_money(late_fee_cents),
            "total_after_late_fee": cents_to_money(
                amount_remaining + late_fee_cents
            ),
            "invoice_url": stripe_get(invoice, "hosted_invoice_url"),
            "reason": "overdue under contract logic",
            "subscription_id": subscription_id,
            "next_invoice_date": next_invoice_date,
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

# If we ran today,who would receive a late fee and how much?
@main.route("/admin/audit-late-fees")
def audit_late_fees():
    if not session.get("logged_in"):
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

# Helper function
# business logic
def apply_late_fee_to_invoice(invoice_id):   
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    now = datetime.now(timezone.utc)
    late_fee_month = now.strftime("%Y-%m")

    # helper functions
    def stripe_get(obj, key, default=None):
        if obj is None:
            return default
        if key in obj:
            return obj[key]
        return default

    def cents_to_money(cents):
        return round((cents / 100), 2)

    def calculate_late_fee_cents(amount_remaining_cents):
        return int(round(amount_remaining_cents * 0.015))

    def late_fee_already_exists(customer_id, source_invoice_id, late_fee_month):
        invoice_items= stripe.InvoiceItem.list(
            customer= customer_id,
        )

        for invoice_item in invoice_items.auto_paging_iter():
            metadata= stripe_get(invoice_item, "metadata", {})

            if (
                stripe_get(metadata, "type") == "late_fee"
                and stripe_get(metadata, "source_invoice_id") == source_invoice_id
                and stripe_get(metadata, "late_fee_month") == late_fee_month
            ):
                return True
            
        return False

    # retrieve invoice
    invoice = stripe.Invoice.retrieve(invoice_id)
    invoice_status= stripe_get(invoice, "status")
    amount_remaining= stripe_get(invoice, "amount_remaining", 0)
    customer_id= stripe_get(invoice, "customer")
    due_date_ts= stripe_get(invoice, "due_date")
    created_ts= stripe_get(invoice, "created")

    # validate invoice is open
    if invoice_status != "open":
        return {
            "status": "skipped",
            "reason": "Invoice is not open. ",
            "invoice_id": invoice_id,
            "invoice_status": invoice_status
        }

    # validate amount remaining
    if amount_remaining <= 0:
        return {
            "status": "skipped",
            "reason": "Invoice has no remaining balance", 
            "invoice_id": invoice_id
        }

    # calculate effective due date using contract logic
    if due_date_ts:
        effective_due_date = datetime.fromtimestamp(due_date_ts, tz=timezone.utc)
    else:
        effective_due_date = datetime.fromtimestamp(created_ts, tz=timezone.utc) + timedelta(days=20)

    # calculate days overdue
    days_overdue = (now - effective_due_date).days
    # days_overdue = 10
    
    if days_overdue <= 0:
        return {
            "status": "skipped",
            "reason": "Invoice is not overdue", 
            "invoice_id": invoice_id,
            "days_overdue": days_overdue
        }

    # idempotency check
    # check whether late fee already exists for: customer_id + invoice_id + late_fee_month
    if late_fee_already_exists(customer_id, invoice_id, late_fee_month):
        return {
            "status": "skipped",
            "reason": "Late fee already exists.",
            "invoice_id": invoice_id,
            "late_fee_month": late_fee_month
        }

    # calculate late fee
    late_fee_cents = calculate_late_fee_cents(amount_remaining)

    # create Stripe invoice item
    invoice_item= stripe.InvoiceItem.create(
        customer=customer_id,
        amount=late_fee_cents,
        discountable=False,
        currency="cad",
        description=f"Late payment charge - {late_fee_month} - invoice {invoice_id}",
        metadata={
            "type" : "late_fee",
            "source_invoice_id" : invoice_id,
            "late_fee_month" : late_fee_month
            }
    )

    # return success response
    return {
        "status": "success",
        "invoice_id": invoice_id,
        "late_fee_month": late_fee_month,
        "late_fee": cents_to_money(late_fee_cents), 
        "invoice_item_id": invoice_item.id
    }

# apply late fee for one person before apply to all
@main.route("/admin/apply-late-fee-one/<invoice_id>", methods=["POST"])
def apply_late_fee_one(invoice_id):
    # if not session.get("logged_in"):
    #     return redirect("/login")
    
    confirm= request.form.get("confirm")

    if confirm != "APPLY": 
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route. "
        }, 400
    
    result= apply_late_fee_to_invoice(invoice_id)

    return result

# Apply late fee to everyone
@main.route("/admin/apply-late-fees", methods=["POST"])
def apply_late_fees():
    # if not session.get("logged_in"):
    #     return redirect("/login")
    
    confirm = request.form.get("confirm")

    if confirm != "APPLY":
        return {
            "error": "Confirmation required. Submit confirm=APPLY to run this route. "
        }, 400
    
    audit_result= find_late_fee_candidates()

    candidates= audit_result["candidates"]

    results = []

    for candidate in candidates: 
        if not candidate["eligible_to_apply"]:
            continue

        try:
            result= apply_late_fee_to_invoice(candidate["invoice_id"])
            results.append(result)

        except Exception as e:
            results.append({
                "status": "failed", 
                "invoice_id": candidate["invoice_id"],
                "error": str(e)
            })

    already_applied_count= sum(1 for c in candidates if not c["eligible_to_apply"])

    return {
        "status": "completed", 
        "total_candidates": len(candidates),
        "eligible_count": sum(1 for c in candidates if c["eligible_to_apply"]),
        "success_count": sum(1 for r in results if r["status"] == "success"),
        "already_applied_count": already_applied_count,
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