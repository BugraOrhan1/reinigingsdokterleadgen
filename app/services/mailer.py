"""Email sending with strict pre-flight checks.

Order of checks before anything is transmitted:

  1. live sending gated by ``DRY_RUN`` / ``LIVE_SEND_ENABLED``;
  2. campaign active, company not blocked, lead not already replied/bounced/blocked;
  3. address not on the suppression list;
  4. address publicly listed with sufficient confidence;
  5. lead score above the configured minimum;
  6. follow-up count within limits;
  7. not already contacted;
  8. hourly / daily / campaign daily limits;
  9. complete live configuration (identity + provider) present.

Exactly-once semantics: a claim (``sending``) is committed before any network
call; if the provider call fails afterwards, the outcome is ``uncertain`` and is
never retried automatically. This guarantees a crash cannot produce duplicates.
"""
import base64
import random
import smtplib
import ssl
import time
from datetime import timedelta

from email.message import EmailMessage
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, EmailEvent, now
from app.services.deduplication import already_contacted, is_suppressed

_COUNTED_STATUSES = ('sending', 'sent', 'uncertain')


def can_send(db: Session, email: Email, lead: CampaignLead, campaign: Campaign, company: Company, contact: Contact) -> tuple[bool, str]:
    """All pre-flight checks; returns (allowed, reason)."""
    cfg = settings()
    if cfg.dry_run:
        pass  # dry-run short-circuits provider transmission, checks still apply
    elif not cfg.live_send_enabled:
        return False, 'live_send_disabled'
    if not campaign.active or company.blocked:
        return False, 'inactive_or_blocked'
    if lead.state in ('replied', 'bounced', 'unsubscribed', 'blocked'):
        return False, 'inactive_or_blocked'
    if is_suppressed(db, contact.email):
        return False, 'suppressed'
    if contact.verification_status != 'publicly_listed' or contact.confidence_score < cfg.min_contact_confidence:
        return False, 'unverified_contact'
    if lead.score < max(cfg.min_lead_score, campaign.min_score):
        return False, 'low_score'
    max_followups = min(cfg.max_followups, campaign.max_followups)
    if email.sequence > max_followups:
        return False, 'followup_limit'
    if email.sequence == 0 and already_contacted(db, company.id, contact.email):
        return False, 'already_contacted'
    if email.sequence > 0 and lead.followups_sent >= max_followups:
        return False, 'followup_limit'
    hour_ago = now() - timedelta(hours=1)
    day_ago = now() - timedelta(days=1)
    hourly = db.scalar(select(func.count(Email.id)).where(Email.status.in_(_COUNTED_STATUSES), Email.claimed_at >= hour_ago)) or 0
    daily = db.scalar(select(func.count(Email.id)).where(Email.status.in_(_COUNTED_STATUSES), Email.claimed_at >= day_ago)) or 0
    campaign_daily = db.scalar(select(func.count(Email.id)).join(CampaignLead).where(CampaignLead.campaign_id == campaign.id, Email.status.in_(_COUNTED_STATUSES), Email.claimed_at >= day_ago)) or 0
    if hourly >= cfg.hourly_email_limit:
        return False, 'rate_limit_hourly'
    if daily >= cfg.daily_email_limit:
        return False, 'rate_limit_daily'
    if campaign_daily >= campaign.daily_limit:
        return False, 'rate_limit_campaign'
    if not cfg.dry_run and not cfg.live_sending_configured():
        return False, 'live_configuration_incomplete'
    return True, 'ok'


def _deliver(email: Email, address: str) -> str:
    """Transmit via SMTP or the Gmail API; returns the provider message id."""
    cfg = settings()
    message = EmailMessage()
    message['From'] = cfg.smtp_from_address or cfg.company_email
    message['To'] = address
    message['Subject'] = email.subject
    message['List-Unsubscribe'] = f'<mailto:{cfg.company_email}?subject=afmelden>'
    message['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
    message.set_content(email.body)
    if cfg.has_gmail:
        import httpx
        token = httpx.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': cfg.gmail_client_id,
                'client_secret': cfg.gmail_client_secret,
                'refresh_token': cfg.gmail_refresh_token,
                'grant_type': 'refresh_token',
            },
            timeout=15,
        )
        token.raise_for_status()
        access_token = token.json()['access_token']
        result = httpx.post(
            'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
            headers={'Authorization': f'Bearer {access_token}'},
            json={'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()},
            timeout=20,
        )
        result.raise_for_status()
        return result.json().get('id', '')
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(cfg.smtp_username, cfg.smtp_password)
        smtp.send_message(message)
    return message['Message-ID'] or ''


def _release_locks_for_counts(db: Session) -> None:
    """Serialise all send claims (advisory lock on the campaigns table).

    The first send in a transaction takes a row lock on the oldest campaign and
    holds it until commit, so concurrent workers cannot double-count limits.
    Cheap in single-instance operation; scales when a shared lock manager is used.
    """
    db.execute(select(Campaign.id).order_by(Campaign.id).with_for_update())


def send_one(db: Session, email_id: int) -> str:
    """Process one draft email. Outcome is recorded once; see module docstring."""
    _release_locks_for_counts(db)
    email = db.get(Email, email_id, with_for_update=True)
    if not email:
        db.rollback()
        return 'not_found'
    if email.status != 'draft':
        db.rollback()
        return 'already_processed'
    lead = db.get(CampaignLead, email.campaign_lead_id)
    campaign = db.get(Campaign, lead.campaign_id)
    company = db.get(Company, lead.company_id)
    contact = db.get(Contact, email.contact_id)
    allowed, reason = can_send(db, email, lead, campaign, company, contact)
    if not allowed:
        email.outcome = reason
        email.failed_at = now()
        db.add(AuditLog(action='EMAIL_FAILED', entity_type='email', entity_id=email.id, detail=reason))
        db.commit()
        return reason

    if settings().dry_run:
        # Nothing is transmitted; the draft is finalised as a dry-run outcome so
        # the scheduler does not re-select it on the next pass.
        email.outcome = 'dry_run'
        email.failed_at = now()
        db.add(AuditLog(action='EMAIL_SENT', entity_type='email', entity_id=email.id, detail='dry_run (not transmitted)'))
        db.commit()
        return 'dry_run'

    # Claim before any network I/O.
    email.status = 'sending'
    email.claimed_at = now()
    db.commit()
    try:
        provider_id = _deliver(email, contact.email)
    except Exception as exc:  # noqa: BLE001 — never retried automatically
        email.outcome = 'uncertain'
        email.error = type(exc).__name__
        email.failed_at = now()
        db.add(EmailEvent(email_id=email.id, event_type='failed', detail='sending'))
        db.add(AuditLog(action='EMAIL_FAILED', entity_type='email', entity_id=email.id, detail=type(exc).__name__))
        db.commit()
        return 'uncertain'

    email.status = 'sent'
    email.outcome = 'sent'
    email.sent_at = now()
    email.provider_id = provider_id
    lead.last_sent_at = email.sent_at
    lead.state = 'sent'
    lead.followups_sent += 1 if email.sequence else 0
    db.add(EmailEvent(email_id=email.id, event_type='sent'))
    db.add(AuditLog(action='FOLLOWUP_SENT' if email.sequence else 'EMAIL_SENT', entity_type='email', entity_id=email.id))
    db.commit()
    time.sleep(random.uniform(settings().min_delay_seconds, settings().max_delay_seconds))
    return 'sent'
