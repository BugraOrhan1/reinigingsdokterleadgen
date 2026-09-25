"""Worker bootstrap: wait for the schema (migrated by the app service), then
start the scheduler. Avoids racing two concurrent `alembic upgrade head` runs.
"""
import sys
import time

from sqlalchemy import create_engine, inspect

from app.config import settings


def wait_for_schema(timeout: int = 180) -> None:
    engine = create_engine(settings().database_url)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if inspect(engine).has_table('companies') and inspect(engine).has_table('emails'):
                return
        except Exception:  # noqa: BLE001 — keep retrying while Postgres warms up
            pass
        time.sleep(2)
    raise SystemExit('Schema did not appear in time; is the app service running migrations?')


def main() -> int:
    wait_for_schema()
    from app.workers.scheduler import main as scheduler_main

    scheduler_main()
    return 0


if __name__ == '__main__':
    sys.exit(main())
