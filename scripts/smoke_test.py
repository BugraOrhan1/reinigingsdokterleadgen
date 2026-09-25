"""End-to-end smoke test of the worker pipeline with a local SQLite DB.

Run: DATABASE_URL=sqlite:////tmp/rd_smoke.db python scripts/smoke_test.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/rd_smoke.db')
os.environ.setdefault('APP_SECRET', 'smoke-secret')
os.environ.setdefault('ADMIN_PASSWORD', 'smoke-pass')
os.environ.setdefault('DRY_RUN', 'true')

from sqlalchemy import create_engine, select  # noqa: E402

from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401
from app.database import SessionLocal  # noqa: E402
from app.models import Campaign, CampaignLead, Contact, Email, now  # noqa: E402
from app.workers.tasks import (  # noqa: E402
    add_company, find_contacts_task, generate_emails, qualify_leads, schedule_followups_task, send_approved_emails, update_statistics,
)


def main() -> int:
    engine = create_engine(os.environ['DATABASE_URL'])
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        campaign = Campaign(name='Restaurants Zuid-Holland', industries=['restaurant'], region='Zuid-Holland', daily_limit=25, min_score=70, max_followups=1, active=True)
        db.add(campaign)
        db.commit()
        data = dict(
            company_name='Eetcafé De Kade', website='https://dekade-rotterdam.nl', industry='restaurant',
            city='Rotterdam', region='Zuid-Holland', address='Westzeedijk 1', source='manual', source_url='https://dekade-rotterdam.nl',
        )
        company = add_company(db, data)
        db.add(CampaignLead(campaign_id=campaign.id, company_id=company.id))
        company.facts = {'evidence': [{'text': 'Eetcafé aan de Maas', 'url': 'https://dekade-rotterdam.nl'}]}
        company.analyzed_at = now()
        db.add(Contact(company_id=company.id, email='info@dekade-rotterdam.nl', email_source_url='https://dekade-rotterdam.nl/contact', contact_type='general', confidence_score=90, verification_status='publicly_listed'))
        db.commit()

    qualify_leads()
    generate_emails()
    # Contact discovery is idempotent: the company already has a contact, so this
    # re-run must not create duplicates.
    find_contacts_task()
    with SessionLocal() as db:
        n_contacts = len(db.scalars(select(Contact)).all())
        print(f'  contacts after find_contacts_task: {n_contacts}')
        for e in db.scalars(select(Email)).all():
            print(f'  email: {e.subject!r} status={e.status} seq={e.sequence}')

    send_approved_emails()
    with SessionLocal() as db:
        for e in db.scalars(select(Email)).all():
            print(f'  after send: status={e.status} outcome={e.outcome}')

    update_statistics()

    with SessionLocal() as db:
        lead = db.scalars(select(CampaignLead)).first()
        lead.state = 'sent'
        lead.last_sent_at = now() - timedelta(days=10)
        lead.followups_sent = 0
        db.commit()
    scheduled = schedule_followups_task()
    print(f'  follow-ups scheduled: {scheduled}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
