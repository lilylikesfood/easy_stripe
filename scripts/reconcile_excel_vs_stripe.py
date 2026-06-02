import os 
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import stripe

from dotenv import load_dotenv

import re

load_dotenv()

stripe.api_key= os.getenv("STRIPE_SECRET_KEY")

EXCEL_FILE= "June_2026_Customer_Master_List.xlsx"

def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def extract_customer_number(description):
    description= str(description).strip()
    match= re.search(r"\d+", description)

    if not match: 
        return None
    
    return match.group()

def build_customer_lookup():
    lookup = {}

    customers = stripe.Customer.list(limit=100)

    for customer in customers.auto_paging_iter():
        if customer.description:
            customer_number = extract_customer_number(customer.description)

            if customer_number:
                lookup[customer_number] = customer

    return lookup

def find_customer_by_description(description):
    customers= stripe.Customer.search(
        query=f"description: '{description}'",
        limit=1
    )

    if not customers.data:
        return None
    
    return customers.data[0]

def get_monthly_service_fee(customer_id):
    subscriptions = stripe.Subscription.list(
        customer=customer_id,
        limit=10
    )

    if not subscriptions.data:
        return None, "no subscription found"

    for sub in subscriptions.data:

        subscription_status = sub["status"]

        for item in sub["items"]["data"]:
            price = item["price"]

            product = stripe.Product.retrieve(price["product"])
            product_name = product["name"].lower()

            if "monthly fee" in product_name:
                amount = Decimal(price["unit_amount"]) / Decimal("100")

                return {
                    "amount": money(amount),
                    "status": subscription_status
                }, None

    return None, "monthly fee item not found"

def main():
    print("MAIN STRARTED")
    if not stripe.api_key:
        raise Exception("Missing STRIPE_SECRET_KEY environment variable")

    df = pd.read_excel(EXCEL_FILE)

    customer_lookup = build_customer_lookup()

    results = []

    for _, row in df.iterrows():
        description = str(row["Description"]).strip()
        customer_number = extract_customer_number(description)
        expected = money(row["New 2026"])

        customer = customer_lookup.get(customer_number)

        if customer is None:
            results.append({
                "Description": description,
                "Excel New 2026": expected,
                "Stripe Monthly Fee": "",
                "Status": "MISSING CUSTOMER",
                "Notes": "No Stripe customer found with this description"
            })
            continue

        subscription_info, error = get_monthly_service_fee(customer.id)

        if error:
            results.append({
                "Description": description,
                "Customer Name": customer.name,
                "Customer Email": customer.email,
                "Excel New 2026": expected,
                "Stripe Monthly Fee": "",
                "Subscription Status": "",
                "Status": "ERROR",
                "Notes": error
            })
            continue

        stripe_amount = subscription_info["amount"]
        subscription_status = subscription_info["status"]

        if stripe_amount == expected:
            status = "MATCH"
            notes = ""
        else:
            status = "MISMATCH"
            notes = f"Excel {expected} vs Stripe {stripe_amount}"

        results.append({
            "Description": description,
            "Customer Name": customer.name,
            "Customer Email": customer.email,
            "Excel New 2026": expected,
            "Stripe Monthly Fee": stripe_amount,
            "Subscription Status": subscription_status,
            "Status": status,
            "Notes": notes
        })

    result_df = pd.DataFrame(results)

    non_active_df = result_df[
        result_df["Subscription Status"] != "active"
    ].sort_values("Subscription Status")

    non_active_df.to_excel(
        "non_active_subscriptions.xlsx",
        index=False
    )

    result_df.to_excel("reconciliation_report.xlsx", index=False)

    print("Done.")
    print(result_df["Status"].value_counts())
    print("Report saved as reconciliation_report.xlsx")
    print("Non-active report saved as non_active_subscriptions.xlsx")
    
if __name__ == "__main__":
    main()