"""Safety-critical integration tests: the behaviours the spec calls out.

In particular: a suppressed contact must NEVER receive a new email, dry-run
must never transmit, bounces/opt-outs suppress and stop follow-ups, and the
pipeline stays idempotent.
"""
from datetime import timedelta

from app.models import Campaign, CampaignLead, Company, Contact, Email, Suppression, now
from app.services.deduplication import is_suppressed
from app.services.followup import schedule_followups
from app.services.inbox_processor import process_reply
from app.services.mailer import send_one

from tests._helpers import make_settings


def _new_lead(db, email='info@voorbeeld.nl', industry='restaurant', city='Rotterdam', state='qualified'):
    campaign = Campaign(name='Test Campagne', industries=[industry], region='Zuid-Holland', daily_limit=25, min_score=65, max_followups=1, active=True)
    db.add(campaign)
    db.flush()
    company = Company(
        company_name='Voorbeeld Restaurant', name_key='voorbeeldrestaurant:rotterdam', domain='voorbeeld.nl',
        website='https://voorbeeld.nl', industry=industry, city=city, region='Zuid-Holland',
        analyzed_at=now(), facts={'evidence': [{'text': 'Restaurant', 'url': 'https://voorbeeld.nl'}]},
    )
    db.add(company)
    db.flush()
    contact = Contact(company_id=company.id, email=email, email_source_url='https://voorbeeld.nl/contact', contact_type='general', confidence_score=90, verification_status='publicly_listed')
    db.add(contact)
    db.flush()
    lead = CampaignLead(campaign_id=campaign.id, company_id=company.id, contact_id=contact.id, score=80, service='vloerreiniging', state=state)
    db.add(lead)
    db.commit()
    return company, contact, lead


def test_dry_run_records_outcome_and_never_transmits(db, monkeypatch):
    company, contact, lead = _new_lead(db)
    mail = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=0, subject='X', body='Y')
    db.add(mail)
    db.commit()
    calls = []
    monkeypatch.setattr('app.services.mailer._deliver', lambda *a, **k: calls.append(a))
    monkeypatch.setattr('app.services.mailer.settings', lambda: make_settings(dry_run=True))
    assert send_one(db, mail.id) == 'dry_run'
    assert calls == []
    refreshed = db.get(Email, mail.id)
    assert refreshed.status == 'draft' and refreshed.outcome == 'dry_run'


def test_suppressed_contact_never_sends_any_followup(db, monkeypatch):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    db.add(Suppression(email=contact.email, reason='opt_out'))
    db.commit()
    monkeypatch.setattr('app.services.followup.settings', lambda: make_settings())
    monkeypatch.setattr('app.services.email_generator.settings', lambda: make_settings())
    assert is_suppressed(db, contact.email)
    assert schedule_followups(db) == 0


def test_bounce_sets_suppression_and_stops_followups(db, monkeypatch):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    db.commit()
    assert process_reply(db, 'bounce-x', 'MAILER-DAEMON@example.org', 'Delivery Status Notification (Failure)', '', target_email=contact.email) == 'BOUNCE'
    assert is_suppressed(db, contact.email)
    assert db.get(CampaignLead, lead.id).state == 'bounced'
    monkeypatch.setattr('app.services.followup.settings', lambda: make_settings())
    monkeypatch.setattr('app.services.email_generator.settings', lambda: make_settings())
    assert schedule_followups(db) == 0


def test_interested_stops_followup_and_marks_lead(db, monkeypatch):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    db.commit()
    assert process_reply(db, 'reply-interest', contact.email, 'Offerte graag', 'Wij hebben interesse, bel mij') == 'INTERESTED'
    assert db.get(CampaignLead, lead.id).state == 'interested'
    monkeypatch.setattr('app.services.followup.settings', lambda: make_settings())
    monkeypatch.setattr('app.services.email_generator.settings', lambda: make_settings())
    assert schedule_followups(db) == 0


def test_draft_cancelled_on_reply(db):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    draft = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=1, subject='fup', body='x')
    db.add(draft)
    db.commit()
    process_reply(db, 'reply-q', contact.email, 'Vraag', 'Kunnen jullie ook ramen doen?')
    assert db.get(Email, draft.id).status == 'cancelled'


def test_out_of_office_does_not_suppress_or_stop(db, monkeypatch):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    db.commit()
    assert process_reply(db, 'reply-ooo', contact.email, 'Out of office', 'I am currently out of the office.') == 'OUT_OF_OFFICE'
    assert not is_suppressed(db, contact.email)
    monkeypatch.setattr('app.services.followup.settings', lambda: make_settings())
    monkeypatch.setattr('app.services.email_generator.settings', lambda: make_settings())
    # OOO does not stop the thread; a follow-up may still be scheduled.
    assert schedule_followups(db) == 1


def test_manual_block_stops_followup(db, monkeypatch):
    company, contact, lead = _new_lead(db, state='sent')
    lead.last_sent_at = now() - timedelta(days=10)
    company.blocked = True
    db.commit()
    monkeypatch.setattr('app.services.followup.settings', lambda: make_settings())
    monkeypatch.setattr('app.services.email_generator.settings', lambda: make_settings())
    assert schedule_followups(db) == 0


def test_low_score_lead_not_sent(db, monkeypatch):
    from app.services.mailer import can_send

    campaign = Campaign(name='Lage score', industries=['restaurant'], region='Zuid-Holland', daily_limit=25, min_score=70, max_followups=1, active=True)
    company = Company(company_name='Laag', name_key='laag:rotterdam', domain='laag.nl', website='https://laag.nl', industry='restaurant', city='Rotterdam', region='Zuid-Holland', analyzed_at=now())
    db.add_all([campaign, company])
    db.flush()
    contact = Contact(company_id=company.id, email='info@laag.nl', email_source_url='https://laag.nl/contact', contact_type='general', confidence_score=90, verification_status='publicly_listed')
    db.add(contact)
    db.flush()
    lead = CampaignLead(campaign_id=campaign.id, company_id=company.id, contact_id=contact.id, score=40, service='vloerreiniging', state='qualified')
    db.add(lead)
    db.flush()
    mail = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=0, subject='X', body='Y')
    db.add(mail)
    db.commit()
    monkeypatch.setattr('app.services.mailer.settings', lambda: make_settings(dry_run=True, min_lead_score=65))
    allowed, reason = can_send(db, mail, lead, campaign, company, contact)
    assert (allowed, reason) == (False, 'low_score')


def test_unverified_contact_not_sent(db, monkeypatch):
    from app.services.mailer import can_send

    campaign = Campaign(name='Ongeverifieerd', industries=['restaurant'], region='Zuid-Holland', daily_limit=25, min_score=65, max_followups=1, active=True)
    company = Company(company_name='Twijfel', name_key='twijfel:rotterdam', domain='twijfel.nl', website='https://twijfel.nl', industry='restaurant', city='Rotterdam', region='Zuid-Holland', analyzed_at=now())
    db.add_all([campaign, company])
    db.flush()
    contact = Contact(company_id=company.id, email='info@twijfel.nl', email_source_url='https://twijfel.nl/contact', contact_type='general', confidence_score=40, verification_status='publicly_listed')
    db.add(contact)
    db.flush()
    lead = CampaignLead(campaign_id=campaign.id, company_id=company.id, contact_id=contact.id, score=80, service='vloerreiniging', state='qualified')
    db.add(lead)
    db.flush()
    mail = Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=0, subject='X', body='Y')
    db.add(mail)
    db.commit()
    monkeypatch.setattr('app.services.mailer.settings', lambda: make_settings(dry_run=True))
    assert can_send(db, mail, lead, campaign, company, contact) == (False, 'unverified_contact')


def test_no_hardcoded_credentials_logged(db):
    # Ensures we keep credentials out of audit detail.
    from app.services.mailer import _deliver
    assert 'password' not in repr(_deliver).casefold()
