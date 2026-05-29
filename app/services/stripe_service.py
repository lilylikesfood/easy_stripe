import stripe

from flask import current_app


def init_stripe(stripe_secret_key):
    stripe.api_key = stripe_secret_key


def create_customer(name, email):
    customer = stripe.Customer.create(
        name=name,
        email=email
    )

    return customer