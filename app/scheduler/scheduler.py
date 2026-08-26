from apscheduler.schedulers.background import BackgroundScheduler
from app.services.automation_service import AutomationService

import os

import uuid
from datetime import datetime, timezone

import atexit
from threading import Lock

import pytz

TORONTO_TZ= pytz.timezone("America/Toronto")

scheduler = BackgroundScheduler(timezone=TORONTO_TZ)

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

atexit.register(shutdown_scheduler)
# notice sth important, () calls the function immediately, so no ()

job_lock= Lock()

# Lock(): preventing two copies of the same job from running at once
overdue_billing_job_lock = Lock()
inspection_fee_removal_job_lock = Lock()
contract_end_50yr_job_lock = Lock()

# scheduler runs daily
# logic runs only on June 1
def start_scheduler(app):
    def job_wrapper():
        with job_lock:
            run_id = str(uuid.uuid4())
            started_at = datetime.now(timezone.utc)

            with app.app_context():
                AutomationService.process_annual_increases(
                    run_id=run_id,
                    started_at=started_at
                )

    def overdue_billing_job_wrapper():
        with overdue_billing_job_lock:
            with app.app_context():
                # Import here (inside the function), not at the top of
                # the file, to avoid circular imports between
                # scheduler.py and main_routes.py
                from app.routes.main_routes import (
                    find_late_fee_candidates,
                    apply_late_fee_to_invoice,
                    find_carry_forward_candidates,
                    carry_forward_invoice_balance,
                    create_carry_forward_log_from_result,
                    is_live_mode,
                )
                from app.extensions import db
                from app.models.late_fee_log import LateFeeLog

                run_id = str(uuid.uuid4())

                print(f"OVERDUE BILLING JOB STARTED: {run_id}")

                # STEP 1: late fees
                late_fee_audit = find_late_fee_candidates()
                late_fee_candidates = late_fee_audit["candidates"]

                for candidate in late_fee_candidates:
                    if not candidate["eligible_to_apply"]:
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

                # STEP 2: carry forwards (after late fees, same order as your manual route)
                carry_forward_audit = find_carry_forward_candidates()
                carry_forward_candidates = carry_forward_audit["candidates"]

                for candidate in carry_forward_candidates:
                    if not candidate["eligible_to_apply"]:
                        continue

                    try:
                        result = carry_forward_invoice_balance(candidate["invoice_id"])
                        log = create_carry_forward_log_from_result(run_id, result)
                        db.session.add(log)

                    except Exception as e:
                        from app.models.carry_forward_log import CarryForwardLog

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

                print(f"OVERDUE BILLING JOB FINISHED: {run_id}")

    def inspection_fee_removal_job_wrapper():
        with inspection_fee_removal_job_lock:
            with app.app_context():
                # Import here (inside the function), not at the top of
                # the file, to avoid circular imports between
                # scheduler.py and main_routes.py
                from app.routes.main_routes import (
                    find_expired_inspection_fee_candidates,
                    apply_expired_inspection_fee_internal,
                    create_inspection_fee_removal_log_from_result,
                    is_live_mode,
                )
                from app.extensions import db

                run_id = str(uuid.uuid4())

                print(f"INSPECTION FEE REMOVAL JOB STARTED: {run_id}")

                mode = "live" if is_live_mode() else "test"

                audit_result = find_expired_inspection_fee_candidates()
                candidates = [
                    candidate
                    for candidate in audit_result["candidates"]
                    if candidate["safe_to_apply"] is True
                ]

                for candidate in candidates:
                    try:
                        result = apply_expired_inspection_fee_internal(
                            candidate["subscription_id"],
                            mode,
                        )

                        log = create_inspection_fee_removal_log_from_result(run_id, result)

                        if log:
                            db.session.add(log)

                    except Exception as e:
                        from app.models.inspection_fee_removal_log import InspectionFeeRemovalLog

                        log = InspectionFeeRemovalLog(
                            run_id=run_id,
                            subscription_id=candidate["subscription_id"],
                            customer_id=candidate.get("customer_id"),
                            inspection_item_id=candidate.get("inspection_item_id"),
                            status="failed",
                            reason=None,
                            error=str(e),
                            created_at=datetime.now(timezone.utc),
                            livemode=is_live_mode(),
                        )
                        db.session.add(log)

                db.session.commit()

                print(f"INSPECTION FEE REMOVAL JOB FINISHED: {run_id}")

    def contract_end_50yr_job_wrapper():
        with contract_end_50yr_job_lock:
            with app.app_context():
                from app.routes.main_routes import (
                    find_contract_end_50yr_candidates,
                    apply_contract_end_50yr_internal,
                    create_contract_end_50yr_log_from_result,
                    is_live_mode,
                )
                from app.extensions import db

                run_id = str(uuid.uuid4())

                print(f"CONTRACT END 50YR JOB STARTED: {run_id}")

                mode = "live" if is_live_mode() else "test"

                audit_result = find_contract_end_50yr_candidates()
                candidates = [
                    candidate
                    for candidate in audit_result["candidates"]
                    if candidate["safe_to_apply"] is True
                ]

                for candidate in candidates:
                    try:
                        result = apply_contract_end_50yr_internal(
                            candidate["subscription_id"],
                            mode,
                        )

                        log = create_contract_end_50yr_log_from_result(run_id, result)

                        if log:
                            db.session.add(log)

                    except Exception as e:
                        from app.models.contract_end_50yr_log import ContractEnd50yrLog

                        log = ContractEnd50yrLog(
                            run_id=run_id,
                            subscription_id=candidate["subscription_id"],
                            customer_id=candidate.get("customer_id"),
                            status="failed",
                            reason=None,
                            error=str(e),
                            created_at=datetime.now(timezone.utc),
                            livemode=is_live_mode(),
                        )
                        db.session.add(log)

                db.session.commit()

                print(f"CONTRACT END 50YR JOB FINISHED: {run_id}")

    print("SCHEDULER PID:", os.getpid())

    # Annual 3% price increase — runs once a year
    # Run every day at 6:05 AM server time
    scheduler.add_job(
        func=job_wrapper,
        # Run this job based on a time schedule (like a calendar rule)
        trigger="cron",
        month=6,
        day=1,
        hour=6,
        minute=5,
        id="annual_increase_job",
        # testing
        # trigger="interval",
        # seconds=20,
    )

    # Daily late fee + carry forward run — runs every day at 7:05 AM
    scheduler.add_job(
        func=overdue_billing_job_wrapper,
        trigger="cron",
        hour=7,
        minute=5,
        # testing
        # trigger="interval",
        # seconds=20,
        id="overdue_billing_job",
    )

    # Daily inspection fee removal — runs every day at 8:05 AM
    scheduler.add_job(
        func=inspection_fee_removal_job_wrapper,
        trigger="cron",
        hour=8,
        minute=5,
        # testing
        # trigger="interval",
        # seconds=20,
        id="inspection_fee_removal_job",
    )

    # Daily 50-year contract end — runs every day at 9:05 AM
    scheduler.add_job(
        func=contract_end_50yr_job_wrapper,
        trigger="cron",
        hour=9,
        minute=5,
        # testing
        # trigger="interval",
        # seconds=20,
        id="contract_end_50yr_job",
    )

    print("REGISTERED JOBS:", scheduler.get_jobs())

    scheduler.start()

    print("Scheduler started: annual increase job active")