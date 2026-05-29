from datetime import date
from dateutil.relativedelta import relativedelta

from app.extensions import db

from app.models.contract import Contract

from app.services.pricing_service import PricingService

from flask import current_app
import stripe


class AutomationService:

    @staticmethod
    def process_annual_increases():

        today = date.today()

        contracts = Contract.query.filter(
            Contract.contract_active == True,
            Contract.annual_increase_date <= today
        ).all()

        for contract in contracts:

            try:

                result = PricingService.apply_annual_increase(
                    contract.stripe_subscription_id
                )

                if result["status"] == "skipped":
                    print(f"SKIPPED: {contract.stripe_subscription_id}")
                    continue

                contract.annual_increase_date = (
                    contract.annual_increase_date
                    + relativedelta(years=1)
                )

                db.session.commit()

                print(
                    f"SUCCESS: Increased subscription "
                    f"{contract.stripe_subscription_id} "
                    f"from {result['old_amount']} "
                    f"to {result['new_amount']}"
                )

            except Exception as e:

                db.session.rollback()

                print(
                    f"ERROR processing contract {contract.id}: {str(e)}"
                )