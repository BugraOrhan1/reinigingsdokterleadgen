from app.config import Settings


def make_settings(**overrides):
    base = dict(
        app_secret='test-secret-32bytes-long-value-0000',
        admin_username='admin',
        admin_password='test-pass',
        dry_run=True,
        live_send_enabled=False,
        enable_automation=False,
        company_name='Reinigingsdokter',
        company_website='https://www.reinigingsdokter.nl',
    )
    base.update(overrides)
    return Settings(**base)
