import stripe

from datetime import date,datetime, timezone

from app.models.billing_log import BillingIncreaseLog
from app.extensions import db

INCREASE_PERCENTAGE = 0.03


class PricingService:

    @staticmethod
    def apply_annual_increase(subscription_id, run_id, started_at):

        subscription = stripe.Subscription.retrieve(subscription_id)

        # -----------------------------
        # 1. IDEMPOTENCY CHECK
        # -----------------------------
        metadata = subscription["metadata"] or {}

        last_year = metadata["last_increase_year"] \
            if "last_increase_year" in metadata \
            else None

        current_year = str(date.today().year)

        if last_year == current_year:
            log = BillingIncreaseLog(
                run_id=run_id,
                subscription_id=subscription_id,
                status="skipped",
                reason="already applied this year",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

            db.session.add(log)
            db.session.commit()

            return {
                "status": "skipped",
                "reason": "already applied this year"
            }

        # -----------------------------
        # 2. LOOP THROUGH SUBSCRIPTION ITEMS
        # -----------------------------
        subscription_items = subscription["items"]["data"]

        results=[]

        for subscription_item in subscription_items:

            price = subscription_item["price"]
            product_id = price["product"]

            product = stripe.Product.retrieve(product_id)

            print(type(product))
            print(type(product["metadata"]))

            product_metadata = product["metadata"] or {}
            increaseable = str(
                product_metadata["increaseable"]
                if "increaseable" in product_metadata
                else "false"
            ).lower()
            
            # Skip non-increaseable items
            if str(increaseable).lower() != "true":
                continue

            # -----------------------------
            # 3. GET CURRENT PRICE
            # -----------------------------
            current_price_id = price["id"]
            current_price = stripe.Price.retrieve(
                current_price_id
            )

            current_amount = current_price["unit_amount"]

            # -----------------------------
            # 4. CALCULATE NEW AMOUNT
            # -----------------------------
            new_amount = round(
                current_amount * (1 + INCREASE_PERCENTAGE)
            )

            # -----------------------------
            # 5. CREATE NEW STRIPE PRICE
            # -----------------------------
            new_price = stripe.Price.create(
                unit_amount=new_amount,
                currency=current_price["currency"],
                recurring=current_price["recurring"],
                product=current_price["product"],

                metadata={
                    "annual_increase": "true",
                    "increase_year": current_year,
                    "previous_price_id": current_price_id,
                    "source_subscription": subscription_id,
                    "run_id": run_id
                },

                idempotency_key=f"price_{subscription_id}_{product_id}_{date.today().year}"

            )

            # -----------------------------
            # 6. REPLACE SUBSCRIPTION ITEM PRICE
            # -----------------------------
            stripe.Subscription.modify(
                subscription_id,

                items=[
                    {
                        "id": subscription_item["id"],
                        "price": new_price.id
                    }
                ],

                metadata={
                    "last_increase_year": current_year
                },
                proration_behavior="none",
                idempotency_key=f"sub_update_{subscription_id}_{product_id}_{date.today().year}"

            )

            # -----------------------------
            # 7. WRITE SUCCESS LOG
            # -----------------------------
            log = BillingIncreaseLog(
                run_id=run_id,
                subscription_id=subscription_id,
                customer_id=subscription["customer"],
                product_id=product_id,

                stripe_price_id_old=current_price_id,
                stripe_price_id_new=new_price.id,

                old_amount=current_amount,
                new_amount=new_amount,
                increase_percentage=INCREASE_PERCENTAGE,

                status="success",
                reason="price increased successfully by 3%",

                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

            db.session.add(log)
            db.session.commit()

            # return {
            #     "status": "success",
            #     "old_amount": current_amount,
            #     "new_amount": new_amount,
            #     "new_price_id": new_price.id
            # }

            results.append({
                "subscription_id": subscription_id,
                "product_id": product_id,
                "price_id_old": current_price_id,
                "price_id_new": new_price.id,
                "old_amount": current_amount,
                "new_amount": new_amount,
                "new_price_id": new_price.id,
                "status": "success"
            })
        
        if not results: 
            log = BillingIncreaseLog(
                run_id=run_id,
                subscription_id=subscription_id,
                status="skipped",
                reason="no increaseable item found",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )

            db.session.add(log)
            db.session.commit()

            # No increaseable item found
            return {
                "status": "skipped",
                "reason": "no increaseable item found"
            }
    
        return {
            "status": "success",
            "results": results
        }