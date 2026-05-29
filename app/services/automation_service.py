import stripe

from app.services.pricing_service import PricingService
from flask import current_app
from datetime import datetime,timezone

class AutomationService:

    @staticmethod
    def process_annual_increases(run_id, started_at):
        print("🔥 JOB TRIGGERED")

        stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

        now= datetime.now(timezone.utc)

        # ONLY RUN ON JUNE 1
        # if now.month != 6 or now.day != 1:
        if False:
            print("SKIPPED: not June 1")
            return

        subscriptions = stripe.Subscription.list(
            status="active",
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():

            try:

                result = PricingService.apply_annual_increase(
                    subscription.id,
                    run_id=run_id,
                    started_at=started_at
                )

                if result["status"] == "skipped":

                    print(
                        f"SKIPPED: {subscription.id} "
                        f"({result['reason']})"
                    )

                    continue

                print(
                    f"SUCCESS: {subscription.id} "
                    f"{result['old_amount']} "
                    f"→ {result['new_amount']}"
                )

            except Exception as e:
                print("========== ERROR ==========")
                print(type(e))
                print(e)
                print("===========================")