import base64
import random
import smtplib
import ssl
import time
from datetime import timedelta
from email.message import EmailMessage
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.config import settings
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, EmailEvent, now
from app.services.deduplication import already_contacted, is_suppressed


def can_send(db: Session, email: Email, lead: CampaignLead, campaign: Campaign, company: Company, contact: Contact) -> tuple[bool, str]:
    cfg = settings()
    if not cfg.dry_run and not cfg.live_send_enabled:
        return False, 'live_send_disabled'
    if not campaign.active or company.blocked or lead.state in ('replied', 'bounced', 'unsubscribed', 'blocked'):
        return False, 'inactive_or_blocked'
    if is_suppressed(db, contact.email):
        return False, 'suppressed'
    if contact.verification_status != 'publicly_listed' or contact.confidence_score < 60:
        return False, 'unverified_contact'
    if lead.score < max(cfg.min_lead_score, campaign.min_score):
        return False, 'low_score'
    if email.sequence > min(cfg.max_followups, campaign.max_followups):
        return False, 'followup_limit'
    if email.sequence == 0 and already_contacted(db, company.id, contact.email):
        return False, 'already_contacted'
    if email.sequence > 0 and lead.followups_sent >= min(cfg.max_followups, campaign.max_followups):
        return False, 'followup_limit'
    hour = now() - timedelta(hours=1)
    day = now() - timedelta(days=1)
    counted = ('sending', 'sent', 'uncertain')
    hourly = db.scalar(select(func.count(Email.id)).where(Email.status.in_(counted), Email.claimed_at >= hour)) or 0
    daily = db.scalar(select(func.count(Email.id)).where(Email.status.in_(counted), Email.claimed_at >= day)) or 0
    campaign_daily = db.scalar(select(func.count(Email.id)).join(CampaignLead).where(CampaignLead.campaign_id == campaign.id, Email.status.in_(counted), Email.claimed_at >= day)) or 0
    if hourly >= cfg.hourly_email_limit or daily >= cfg.daily_email_limit or campaign_daily >= campaign.daily_limit:
        return False, 'rate_limit'
    if not cfg.dry_run and (not cfg.company_address or not cfg.company_email or not (cfg.smtp_host or cfg.gmail_refresh_token)):
        return False, 'live_configuration_incomplete'
    return True, 'ok'


def _deliver(email: Email, address: str) -> str:
    cfg = settings()
    message = EmailMessage()
    message['From'] = cfg.smtp_from or cfg.company_email
    message['To'] = address
    message['Subject'] = email.subject
    message['List-Unsubscribe'] = f'<mailto:{cfg.company_email}?subject=afmelden>'
    message.set_content(email.body)
    if cfg.gmail_refresh_token:
        import httpx
        token = httpx.post('https://oauth2.googleapis.com/token', data={'client_id': cfg.gmail_client_id, 'client_secret': cfg.gmail_client_secret, 'refresh_token': cfg.gmail_refresh_token, 'grant_type': 'refresh_token'}, timeout=15)
        token.raise_for_status()
        access_token = token.json()['access_token']
        result = httpx.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', headers={'Authorization': f'Bearer {access_token}'}, json={'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}, timeout=20)
        result.raise_for_status()
        return result.json().get('id', '')
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(cfg.smtp_username, cfg.smtp_password)
        smtp.send_message(message)
    return message['Message-ID'] or ''


def send_one(db: Session, email_id: int) -> str:
    # Serialize all send claims in PostgreSQL to make limits and deduplication atomic.
    db.execute(select(Campaign).order_by(Campaign.id).with_for_update())
    email = db.get(Email, email_id, with_for_update=True)
    if not email or email.status != 'draft':
        db.rollback()
        return 'already_processed'
    lead = db.get(CampaignLead, email.campaign_lead_id)
    campaign = db.get(Campaign, lead.campaign_id)
    company = db.get(Company, lead.company_id)
    contact = db.get(Contact, email.contact_id)
    allowed, reason = can_send(db, email, lead, campaign, company, contact)
    if not allowed:
        db.rollback()
        return reason
    if settings().dry_run:
        db.add(AuditLog(action='EMAIL_GENERATED', entity_type='email', entity_id=email.id, detail='dry_run'))
        db.commit()
        return 'dry_run'
    email.status = 'sending'
    email.claimed_at = now()
    db.add(AuditLog(action='EMAIL_GENERATED', entity_type='email', entity_id=email.id, detail='claimed'))
    db.commit()
    # A crash after provider acceptance leaves 'sending'; it is never retried automatically.
    try:
        provider_id = _deliver(email, contact.email)
    except Exception as exc:
        email.status = 'uncertain'
        db.add(AuditLog(action='EMAIL_FAILED', entity_type='email', entity_id=email.id, detail=type(exc).__name__))
        db.commit()
        return 'uncertain'
    email.status = 'sent'
    email.sent_at = now()
    email.provider_id = provider_id
    lead.last_sent_at = email.sent_at
    lead.state = 'sent'
    if email.sequence:
        lead.followups_sent += 1
    db.add(EmailEvent(email_id=email.id, event_type='sent'))
    db.add(AuditLog(action='FOLLOWUP_SENT' if email.sequence else 'EMAIL_SENT', entity_type='email', entity_id=email.id))
    db.commit()
    time.sleep(random.uniform(settings().min_delay_seconds, settings().max_delay_seconds))
    return 'sent'
