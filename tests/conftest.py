import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
import app.models  # noqa: F401


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def sample(db):
    from app.models import Campaign, CampaignLead, Company, Contact, Email, now
    company = Company(company_name='Voorbeeld Restaurant', name_key='voorbeeldrestaurant:rotterdam', domain='voorbeeld.nl', website='https://voorbeeld.nl', industry='restaurant', city='Rotterdam', region='Zuid-Holland', facts={'evidence': [{'text': 'Restaurant', 'url': 'https://voorbeeld.nl'}]}, analyzed_at=now())
    campaign = Campaign(name='Restaurants Zuid-Holland', industries=['restaurant'], region='Zuid-Holland', daily_limit=25, min_score=65, max_followups=1, active=True)
    db.add_all([company, campaign])
    db.flush()
    contact = Contact(company_id=company.id, email='info@voorbeeld.nl', email_source_url='https://voorbeeld.nl/contact', contact_type='general', confidence_score=90, verification_status='publicly_listed')
    db.add(contact)
    db.flush()
    lead = CampaignLead(company_id=company.id, campaign_id=campaign.id, contact_id=contact.id, score=90, service='vloerreiniging', state='qualified')
    db.add(lead)
    db.flush()
    mail = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=0, subject='Vraag over reiniging', body='Goedemiddag')
    db.add(mail)
    db.commit()
    return company, campaign, contact, lead, mail
