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

from app.services.pricing_service import PricingService

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
@main.route("/admin/run-increase-one/<subscription_id>")
def run_increase_one(subscription_id):

    if not session.get("logged_in"):
        return redirect("/login")

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
@main.route("/admin/run-increase-missing-billable")
def run_increase_missing_billable():

    if not session.get("logged_in"):
        return redirect("/login")

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