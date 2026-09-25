from apscheduler.schedulers.blocking import BlockingScheduler
from app.workers.tasks import run_pipeline


def main():
    scheduler = BlockingScheduler(timezone='Europe/Amsterdam')
    scheduler.add_job(run_pipeline, 'interval', hours=1, id='pipeline', max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.start()


if __name__ == '__main__':
    main()
