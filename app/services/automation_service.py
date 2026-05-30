import stripe

from app.services.pricing_service import PricingService
from flask import current_app
from datetime import datetime,timezone, date

from app.models.billing_run_control import BillingRunControl
from app.models.billing_log import BillingIncreaseLog
from app.extensions import db

class AutomationService:

    @staticmethod
    def process_annual_increases(run_id, started_at):

        stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

        run_date = datetime.now(timezone.utc).date().isoformat()

        # 1. CHECK IF RUN EXISTS
        run_control = (
            BillingRunControl.query
            .filter_by(run_date=run_date)
            .first()
        )

        if run_control and run_control.status == "success":
            print("🚫 already ran today")
            return

        # 2. CREATE IF NOT EXISTS
        if not run_control:
            try:
                run_control = BillingRunControl(
                    run_date=run_date,
                    run_id=run_id,
                    status="running"
                )
                db.session.add(run_control)
                db.session.commit()

            except Exception as e:
                db.session.rollback()
                return

        try:

            subscriptions = stripe.Subscription.list(status="active")

            for subscription in subscriptions.auto_paging_iter():
                try: 

                    result=PricingService.apply_annual_increase(
                        subscription.id,
                        run_id=run_id,
                        started_at=started_at
                    )

                    print(result)

                except Exception as e:
                    import traceback

                    traceback.print_exc()

                    log = BillingIncreaseLog(
                    run_id=run_id,
                    subscription_id=subscription.id,
                    status="failed",
                    reason=str(e),
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    )

                    db.session.add(log)
                    db.session.commit()

                    print("FAILED SUB:", subscription.id, e)
                    continue

            # 3. MARK SUCCESS
            if run_control: 
                run_control.status = "success"
                run_control.finished_at = datetime.now(timezone.utc)
                db.session.commit()

        except Exception as e:
            db.session.rollback()

            print(e)

            run_control = BillingRunControl.query.filter_by(run_id=run_id).first()
            if run_control:
                run_control.status = "failed"
                run_control.finished_at = datetime.now(timezone.utc)
                db.session.commit()