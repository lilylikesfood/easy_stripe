import stripe

from datetime import date

INCREASE_PERCENTAGE = 0.03


class PricingService:

    @staticmethod
    def apply_annual_increase(subscription_id):

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

            return {
                "status": "skipped",
                "reason": "already applied this year"
            }

        # -----------------------------
        # 2. LOOP THROUGH SUBSCRIPTION ITEMS
        # -----------------------------
        subscription_items = subscription["items"]["data"]

        for subscription_item in subscription_items:

            price = subscription_item["price"]

            product_id = price["product"]

            product = stripe.Product.retrieve(product_id)

            product_metadata = product["metadata"] or {}

            increaseable = (
                product_metadata["increaseable"]
                if "increaseable" in product_metadata
                else None
            )

            # Skip non-increaseable items
            if increaseable != "true":
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
                recurring={"interval": "month"},
                product=current_price["product"],
                metadata={
                    "annual_increase": "true"
                }
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

                proration_behavior="none"
            )

            # -----------------------------
            # 7. MARK AS PROCESSED
            # -----------------------------
            stripe.Subscription.modify(
                subscription_id,

                metadata={
                    "last_increase_year": current_year
                }
            )

            return {
                "status": "success",
                "old_amount": current_amount,
                "new_amount": new_amount,
                "new_price_id": new_price.id
            }

        # No increaseable item found
        return {
            "status": "skipped",
            "reason": "no increaseable item found"
        }