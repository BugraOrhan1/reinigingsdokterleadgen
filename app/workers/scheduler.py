"""APScheduler entrypoint for the worker container.

Each task is fault-tolerant and idempotent (see ``app.workers.tasks``), so a
crash or an aborted run never results in duplicate emails. ``max_instances=1``
plus ``coalesce=True`` keeps overlapping runs from stacking.
"""
import logging

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from app.workers.tasks import (
    analyze_websites,
    discover_leads,
    find_contacts_task,
    generate_emails,
    process_bounces,
    process_inbox,
    qualify_leads,
    run_pipeline,
    schedule_followups_task,
    send_approved_emails,
    update_statistics,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('rd.scheduler')


def _run(name: str, task) -> None:
    try:
        task()
    except Exception:  # noqa: BLE001
        log.exception('Task %s failed', name)


def main() -> None:
    scheduler = BlockingScheduler(timezone='Europe/Amsterdam')
    scheduler.add_executor(ThreadPoolExecutor(max_workers=3), 'default')

    # One coherent pipeline pass every hour.
    scheduler.add_job(run_pipeline, 'interval', hours=1, id='pipeline', max_instances=1, coalesce=True, misfire_grace_time=300)

    # Fine-grained cadences: send / inbox more regularly, discovery once per day.
    scheduler.add_job(lambda: _run('discover_leads', discover_leads), 'interval', hours=12, id='discover', max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(lambda: _run('analyze_websites', analyze_websites), 'interval', minutes=20, id='analyze', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('find_contacts', find_contacts_task), 'interval', minutes=20, id='contacts', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('qualify_leads', qualify_leads), 'interval', minutes=20, id='qualify', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('generate_emails', generate_emails), 'interval', minutes=20, id='generate', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('send_approved_emails', send_approved_emails), 'interval', minutes=15, id='send', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('process_inbox', process_inbox), 'interval', minutes=5, id='inbox', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('process_bounces', process_bounces), 'interval', minutes=15, id='bounces', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('schedule_followups', schedule_followups_task), 'interval', hours=6, id='followups', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: _run('update_statistics', update_statistics), 'interval', hours=1, id='stats', max_instances=1, coalesce=True)

    log.info('Scheduler started (Europe/Amsterdam)')
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == '__main__':
    main()
