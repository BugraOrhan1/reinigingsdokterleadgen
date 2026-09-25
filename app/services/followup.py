"""Follow-up scheduling after an initial email without a reply/outcome.

No follow-up is queued when:
  * the lead already got a terminating reply (interested/not interested/question);
  * the lead or address is suppressed (opt-out) or the email bounced;
  * the lead is manually blocked or the campaign stopped;
  * the follow-up count would exceed the configured maximum.
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, now
from app.services.deduplication import is_suppressed
from app.services.email_generator import generate_with_llm

_STOPPED_STATES = ('replied', 'interested', 'not_interested', 'unsubscribed', 'bounced', 'blocked')


def schedule_followups(db: Session, min_days: int | None = None) -> int:
    delay_days = min_days if min_days is not None else settings().followup_delay_days
    threshold = now() - timedelta(days=delay_days)
    count = 0
    leads = db.scalars(
        select(CampaignLead).where(
            CampaignLead.state == 'sent',
            CampaignLead.last_sent_at <= threshold,
            CampaignLead.last_sent_at.is_not(None),
        )
    ).all()
    for lead in leads:
        campaign = db.get(Campaign, lead.campaign_id)
        company = db.get(Company, lead.company_id)
        contact = db.get(Contact, lead.contact_id) if lead.contact_id else None
        sequence = lead.followups_sent + 1
        if campaign is None:
            continue
        followup_max = min(campaign.max_followups, settings().max_followups)
        if not campaign.active:
            continue
        if company is None or company.blocked:
            continue
        if lead.state in _STOPPED_STATES:
            continue
        if not contact or is_suppressed(db, contact.email):
            continue
        if sequence > followup_max:
            continue
        if db.scalar(select(Email.id).where(Email.campaign_lead_id == lead.id, Email.sequence == sequence)):
            continue
        subject, body = generate_with_llm(company, lead.service, sequence)
        db.add(Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=sequence, subject=subject, body=body))
        db.add(AuditLog(action='EMAIL_GENERATED', entity_type='campaign_lead', entity_id=lead.id, detail='followup'))
        count += 1
    db.commit()
    return count
