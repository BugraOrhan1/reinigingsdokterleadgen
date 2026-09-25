"""Plaats demo-data zodat het dashboard meteen gevuld is.

Alleen bedoeld om te zien hoe de app werkt. Idempotent: doet niets als er al
bedrijven in de database staan.

Run:   python scripts/seed_demo.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal  # noqa: E402
import app.models  # noqa: E402,F401
from app.models import (  # noqa: E402
    AuditLog, Campaign, CampaignLead, Company, Contact, Email, EmailEvent, Reply, ServiceMapping, Suppression,
)


def _now():
    return datetime.now(timezone.utc)


def main() -> int:
    # The engine is bound at import time; make sure the env var was already set
    # (start.sh and Makefile do this before calling this script).
    db_url = os.environ.get('DATABASE_URL', settings().database_url)
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    with SessionLocal() as db:
        if db.scalars(select(Company.id).limit(1)).all():
            print('Database is al gevuld: demo-data overgeslagen.')
            return 0

        # 1) Dienst-mappings
        for industry, service in {
            'restaurant': 'vloer- en periodieke reiniging',
            'hotel': 'meubel- en vloerreiniging',
            'sportschool': 'vloer- en periodieke reiniging',
            'kantoor': 'kantoor- en vloerreiniging',
            'garage': 'vloer- en intensieve reiniging',
            'winkel': 'vloerreiniging',
            'school': 'vloer- en periodieke reiniging',
            'kinderdagverblijf': 'periodieke reiniging',
        }.items():
            db.add(ServiceMapping(industry=industry, service=service))

        # 2) Bedrijven + algemeen zakelijke contacten
        demo = [
            ('Bistro De Kade', 'bistrodkade.nl', 'restaurant', 'Rotterdam', 'info'),
            ('Grand Hotel Riviera', 'hotelriviera.nl', 'hotel', 'Den Haag', 'info'),
            ('FitPlein 24', 'fitplein24.nl', 'sportschool', 'Rotterdam', 'contact'),
            ('Kantoorhub Zuid', 'kantoorhubzuid.nl', 'kantoor', 'Gouda', 'office'),
            ('Autobedrijf Vliet', 'autovliet.nl', 'garage', 'Delft', 'info'),
            ('Bakkerij De Warme Oven', 'warmeoven.nl', 'winkel', 'Leiden', 'contact'),
            ('BS De Regenboog', 'bsregenboog.nl', 'school', 'Zoetermeer', 'info'),
            ('Kinderopvang Pip', 'kinderopvangpip.nl', 'kinderdagverblijf', 'Rotterdam', 'info'),
        ]
        companies = []
        for name, domain, industry, city, local in demo:
            company = Company(
                company_name=name,
                name_key=''.join(ch for ch in name.casefold() if ch.isalnum()) + ':' + city.casefold(),
                domain=domain,
                website=f'https://{domain}',
                industry=industry,
                city=city,
                region='Zuid-Holland',
                address='Voorbeeldstraat 1',
                source='demo',
                source_url=f'https://{domain}',
                facts={'evidence': [{'text': f'{name} in {city}', 'url': f'https://{domain}'}]},
                analyzed_at=_now(),
            )
            db.add(company)
            db.flush()
            db.add(Contact(
                company_id=company.id,
                email=f'{local}@{domain}',
                email_source_url=f'https://{domain}/contact',
                contact_type='general',
                confidence_score=90,
                verification_status='publicly_listed',
            ))
            companies.append(company)

        # 3) Campagnes (spec-voorbeeld + één extra, gepauzeerd)
        c1 = Campaign(name='Restaurants Zuid-Holland', industries=['restaurant'], region='Zuid-Holland', city='', radius_km=0, daily_limit=25, min_score=70, max_followups=1, active=True)
        c2 = Campaign(name='Hotels Zuid-Holland', industries=['hotel'], region='Zuid-Holland', city='', radius_km=0, daily_limit=25, min_score=65, max_followups=1, active=False)
        db.add_all([c1, c2])
        db.flush()

        # 4) Gekwalificeerde leads + conceptmails
        company_map = {c.company_name: c for c in companies}
        leads = []
        emails = []
        for name, campaign, score, service in [
            ('Bistro De Kade', c1, 92, 'vloer- en periodieke reiniging'),
            ('Grand Hotel Riviera', c2, 84, 'meubel- en vloerreiniging'),
            ('Kantoorhub Zuid', c1, 71, 'kantoor- en vloerreiniging'),
            ('FitPlein 24', c1, 0, ''),  # onder de grens: niet gekwalificeerd
        ]:
            company = company_map[name]
            contact = db.scalar(select(Contact).where(Contact.company_id == company.id))
            state = 'qualified' if score >= 65 else 'rejected'
            lead = CampaignLead(campaign_id=campaign.id, company_id=company.id, contact_id=contact.id, score=score, service=service, state=state)
            db.add(lead)
            db.flush()
            leads.append((lead, company, contact))
            if state == 'qualified':
                mail = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=0, subject=f'Vraag over reiniging bij {company.company_name}', body='Goedemiddag,\n\nIk zag de website van uw bedrijf. Reinigingsdokter helpt bedrijven graag met reiniging. Zou een kort gesprek nuttig zijn?\n\nMet vriendelijke groet,\nReinigingsdokter')
                db.add(mail)
                db.flush()
                emails.append((mail, lead))

        # 5) Eén "verzonden" e-mail zodat Overview ook een verzonden mail toont
        sent, lead = emails[0]
        sent.status = 'sent'
        sent.outcome = 'sent'
        sent.sent_at = _now() - timedelta(days=3)
        db.add(EmailEvent(email_id=sent.id, event_type='sent'))
        db.add(EmailEvent(email_id=sent.id, event_type='delivered'))

        # 6) Een geïnteresseerde reactie (verschijnt bij "Interested Leads")
        lead2, company2, contact2 = leads[1]
        db.add(Reply(external_id='demo-reply-1', contact_id=contact2.id, email_id=sent.id, classification='INTERESTED', snippet='Wij hebben interesse, graag contact opnemen.', received_at=_now() - timedelta(days=1)))
        lead2.state = 'interested'

        # 7) Een opt-out + suppression-item
        optout_email = 'info@warmeoven.nl'
        db.add(Reply(external_id='demo-reply-2', contact_id=None, email_id=None, classification='UNSUBSCRIBE', snippet='Afmelden a.u.b.', received_at=_now() - timedelta(days=2)))
        db.add(Suppression(email=optout_email, reason='unsubscribe'))

        for action in ('LEAD_FOUND', 'WEBSITE_ANALYZED', 'LEAD_QUALIFIED', 'EMAIL_GENERATED', 'EMAIL_SENT', 'REPLY_RECEIVED'):
            db.add(AuditLog(action=action, entity_type='demo', detail='demo-data'))

        db.commit()

    print('Demo-data geplaatst: 8 bedrijven, 2 campagnes, gemailde leads,')
    print('een geïnteresseerde lead en een opt-out.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
