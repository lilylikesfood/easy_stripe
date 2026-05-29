from apscheduler.schedulers.background import BackgroundScheduler
from app.services.automation_service import AutomationService

import os

scheduler = BackgroundScheduler()

# scheduler runs daily
# logic runs only on June 1
def start_scheduler(app):
    def job_wrapper():
        with app.app_context():
            AutomationService.process_annual_increases()

    print("SCHEDULER PID:", os.getpid())

    # Run every day at 00:05 (12:05 AM)server time
    scheduler.add_job(
        func=job_wrapper,
        # Run this job based on a time schedule (like a calendar rule)
        # trigger="cron",
        # hour=0,
        # minute=5,
        id="annual_increase_job",
        # testing
        trigger="interval",
        seconds=10,
    )

    print("REGISTERED JOBS:", scheduler.get_jobs())

    scheduler.start()

    print("Scheduler started: annual increase job active")