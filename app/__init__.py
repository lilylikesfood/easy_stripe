from flask import Flask
from config import Config
from app.extensions import db, migrate

from app.scheduler.scheduler import start_scheduler
from app.services.stripe_service import init_stripe


def create_app():

    app = Flask(__name__)

    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app)

    init_stripe(app.config["STRIPE_SECRET_KEY"])  

    from app.scheduler.scheduler import start_scheduler
    from app.models.contract import Contract
    from app.routes.main_routes import main

    app.register_blueprint(main)

    start_scheduler()

    return app