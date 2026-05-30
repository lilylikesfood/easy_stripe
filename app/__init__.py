from flask import Flask
from config import Config
from app.extensions import db, migrate

from app.services.stripe_service import init_stripe

import os

def create_app():

    app = Flask(__name__)

    app.config.from_object(Config)

    app.secret_key = os.getenv("SECRET_KEY", "dev-key")

    db.init_app(app)
    migrate.init_app(app, db)

    init_stripe(app.config["STRIPE_SECRET_KEY"])  

    # imports AFTER app exists
    from app.scheduler.scheduler import start_scheduler
    from app.routes.main_routes import main

    app.register_blueprint(main)

    # start_scheduler(app)

    return app