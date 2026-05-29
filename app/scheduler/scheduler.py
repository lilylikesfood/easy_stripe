from apscheduler.schedulers.background import BackgroundScheduler
from app.services.automation_service import AutomationService


scheduler = BackgroundScheduler()

def start_scheduler():

    scheduler.add_job(
        func=AutomationService.process_annual_increases,
        trigger="cron",
        hour=0,
        minute=0
    )

    scheduler.start()