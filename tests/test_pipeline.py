from datetime import timedelta
from sqlalchemy import select
from app.config import Settings
from app.models import CampaignLead, Email, Suppression, now
from app.services.deduplication import find_company, is_suppressed
from app.services.email_generator import generate
from app.services.followup import schedule_followups
from app.services.inbox_processor import process_reply
from app.services.lead_qualifier import score_lead
from app.services.mailer import can_send, send_one


def test_duplicate_detection(db, sample):
    company, *_ = sample
    assert find_company(db, 'Other Name', 'https://www.voorbeeld.nl/contact').id == company.id
    assert find_company(db, 'Voorbeeld Restaurant', 'https://different.nl', 'Rotterdam').id == company.id


def test_qualification_and_generation(sample, monkeypatch):
    company, campaign, contact, _, _ = sample
    monkeypatch.setattr('app.services.email_generator.settings', lambda: Settings())
    assert score_lead(company, contact, campaign, 'vloerreiniging') >= 65
    subject, body = generate(company, 'vloerreiniging')
    assert company.company_name in subject + body
    assert 'vloerreiniging' in body
    assert 'afmelden' in body


def test_suppressed_contact_never_sends(db, sample, monkeypatch):
    company, campaign, contact, lead, mail = sample
    db.add(Suppression(email=contact.email, reason='opt_out'))
    db.commit()
    called = []
    monkeypatch.setattr('app.services.mailer._deliver', lambda *args: called.append(args))
    monkeypatch.setattr('app.services.mailer.settings', lambda: Settings(dry_run=False, live_send_enabled=True, company_address='Straat 1', company_email='info@reinigingsdokter.nl', smtp_host='smtp.example'))
    assert is_suppressed(db, contact.email)
    assert send_one(db, mail.id) == 'suppressed'
    assert not called
    assert db.get(Email, mail.id).status == 'draft'


def test_dry_run_never_calls_provider(db, sample, monkeypatch):
    *_, mail = sample
    called = []
    monkeypatch.setattr('app.services.mailer._deliver', lambda *args: called.append(args))
    monkeypatch.setattr('app.services.mailer.settings', lambda: Settings(dry_run=True))
    assert send_one(db, mail.id) == 'dry_run'
    assert not called
    assert db.get(Email, mail.id).status == 'draft'


def test_send_limits(db, sample, monkeypatch):
    company, campaign, contact, lead, mail = sample
    monkeypatch.setattr('app.services.mailer.settings', lambda: Settings(hourly_email_limit=0))
    assert can_send(db, mail, lead, campaign, company, contact) == (False, 'rate_limit')


def test_unsubscribe_stops_followup_and_suppresses(db, sample):
    _, _, contact, lead, _ = sample
    lead.state = 'sent'
    lead.last_sent_at = now() - timedelta(days=10)
    db.commit()
    assert process_reply(db, 'reply-1', contact.email, 'Afmelden', 'Geen mails meer') == 'UNSUBSCRIBE'
    assert db.get(CampaignLead, lead.id).state == 'unsubscribed'
    assert is_suppressed(db, contact.email)
    assert is_suppressed(db, 'office@voorbeeld.nl')
    assert schedule_followups(db) == 0


def test_bounce_target_suppressed(db, sample):
    _, _, contact, lead, _ = sample
    assert process_reply(db, 'bounce-1', 'mailer-daemon@example.org', 'Delivery failed', '', target_email=contact.email) == 'BOUNCE'
    assert db.get(CampaignLead, lead.id).state == 'bounced'
    assert is_suppressed(db, contact.email)


def test_unknown_unsubscribe_is_suppressed(db):
    assert process_reply(db, 'unknown-optout', 'contact@anderbedrijf.nl', 'afmelden', '') == 'UNSUBSCRIBE'
    assert is_suppressed(db, 'contact@anderbedrijf.nl')


def test_followup_at_most_once(db, sample, monkeypatch):
    _, _, _, lead, mail = sample
    monkeypatch.setattr('app.services.email_generator.settings', lambda: Settings())
    monkeypatch.setattr('app.services.followup.settings', lambda: Settings())
    lead.state = 'sent'
    lead.last_sent_at = now() - timedelta(days=10)
    mail.status = 'sent'
    db.commit()
    assert schedule_followups(db) == 1
    assert schedule_followups(db) == 0
    assert db.scalar(select(Email.id).where(Email.campaign_lead_id == lead.id, Email.sequence == 1))
