import stripe

from app.services.pricing_service import PricingService
from flask import current_app


class AutomationService:

    @staticmethod
    def process_annual_increases():
        stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

        subscriptions = stripe.Subscription.list(
            status="active",
            limit=100
        )

        for subscription in subscriptions.auto_paging_iter():

            try:

                result = PricingService.apply_annual_increase(
                    subscription.id
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