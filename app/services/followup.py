from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Campaign, CampaignLead, Company, Contact, Email, AuditLog, now
from app.services.deduplication import is_suppressed
from app.services.email_generator import generate_with_llm


def schedule_followups(db: Session, min_days: int = 7) -> int:
    count = 0
    for lead in db.scalars(select(CampaignLead).where(CampaignLead.state == 'sent', CampaignLead.last_sent_at <= now() - timedelta(days=min_days))).all():
        campaign = db.get(Campaign, lead.campaign_id)
        company = db.get(Company, lead.company_id)
        contact = db.get(Contact, lead.contact_id) if lead.contact_id else None
        sequence = lead.followups_sent + 1
        if not campaign.active or company.blocked or not contact or is_suppressed(db, contact.email) or sequence > min(campaign.max_followups, settings().max_followups):
            continue
        if db.scalar(select(Email.id).where(Email.campaign_lead_id == lead.id, Email.sequence == sequence)):
            continue
        subject, body = generate_with_llm(company, lead.service, sequence)
        db.add(Email(campaign_lead_id=lead.id, contact_id=contact.id, sequence=sequence, subject=subject, body=body))
        db.add(AuditLog(action='EMAIL_GENERATED', entity_type='campaign_lead', entity_id=lead.id, detail='followup'))
        count += 1
    db.commit()
    return count
