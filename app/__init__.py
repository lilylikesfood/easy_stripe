print("1 before flask")
from flask import Flask
print("2 after flask")

from config import Config
print("3 after config")

from app.extensions import db, migrate
print("4 after extensions")


from app.services.stripe_service import init_stripe
print("5 after stripe_service")


import os
print("6 after os")


from dotenv import load_dotenv
print("7 after dotenv import")


load_dotenv()
print("8 after load_dotenv")

def create_app():

    app = Flask(__name__)

    app.config.from_object(Config)

    app.secret_key = os.getenv("SECRET_KEY", "dev-key")

    db.init_app(app)

    # A database model should not depend on whether a web route happens to use it.
    from app.models.billing_log import BillingIncreaseLog
    from app.models.billing_run_control import BillingRunControl
    from app.models.schedule_repair_log import ScheduleRepairLog
    from app.models.late_fee_log import LateFeeLog
    from app.models.carry_forward_log import CarryForwardLog

    migrate.init_app(app, db)

    init_stripe(app.config["STRIPE_SECRET_KEY"])  

    # imports AFTER app exists
    # from app.scheduler.scheduler import start_scheduler
    from app.routes.main_routes import main

    app.register_blueprint(main)

    # start_scheduler(app)

    return app