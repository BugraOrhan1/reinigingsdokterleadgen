from sqlalchemy import select
from app.config import settings
from app.database import SessionLocal
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, ServiceMapping, Setting, now
from app.services.deduplication import domain_of, find_company
from app.services.lead_finder import discover
from app.services.website_analyzer import extract_facts, fetch_pages
from app.services.contact_finder import find_contacts
from app.services.lead_qualifier import DEFAULT_SERVICES, score_lead
from app.services.email_generator import generate_with_llm
from app.services.mailer import send_one
from app.services.followup import schedule_followups
from app.services.inbox_polling import poll_inbox


def add_company(db, data: dict) -> Company:
    existing = find_company(db, data['company_name'], data['website'], data.get('city', ''))
    if existing:
        return existing
    from app.services.deduplication import name_key
    company = Company(**data, domain=domain_of(data['website']), name_key=name_key(data['company_name']) + ':' + name_key(data.get('city', '')))
    db.add(company)
    db.flush()
    db.add(AuditLog(action='LEAD_FOUND', entity_type='company', entity_id=company.id, detail=company.source))
    return company


def discover_leads():
    with SessionLocal() as db:
        for campaign in db.scalars(select(Campaign).where(Campaign.active)).all():
            stamp_key = f'discovery_last_{campaign.id}'
            stamp = db.get(Setting, stamp_key)
            if stamp and stamp.value.get('date') == now().date().isoformat():
                continue
            for industry in campaign.industries:
                for data in discover(industry, campaign.region, campaign.city):
                    company = add_company(db, data)
                    if not db.scalar(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign.id, CampaignLead.company_id == company.id)):
                        db.add(CampaignLead(campaign_id=campaign.id, company_id=company.id))
                db.commit()
            db.merge(Setting(key=stamp_key, value={'date': now().date().isoformat()}))
            db.commit()


def analyze_websites():
    with SessionLocal() as db:
        for company in db.scalars(select(Company).where(Company.analyzed_at.is_(None), Company.blocked.is_(False)).limit(50)).all():
            try:
                pages = fetch_pages(company.website)
                from app.models import now
                company.facts = extract_facts(pages)
                company.analyzed_at = now()
                db.add(AuditLog(action='WEBSITE_ANALYZED', entity_type='company', entity_id=company.id))
                for item in find_contacts(pages, company.website):
                    if not db.scalar(select(Contact.id).where(Contact.email == item['email'])):
                        db.add(Contact(company_id=company.id, **item))
                        db.add(AuditLog(action='CONTACT_FOUND', entity_type='company', entity_id=company.id))
                db.commit()
            except Exception as exc:
                db.rollback()
                db.add(AuditLog(action='WEBSITE_ANALYZED', entity_type='company', entity_id=company.id, detail=type(exc).__name__))
                db.commit()


def qualify_leads():
    with SessionLocal() as db:
        for lead in db.scalars(select(CampaignLead).where(CampaignLead.state == 'new').limit(100)).all():
            company = db.get(Company, lead.company_id)
            campaign = db.get(Campaign, lead.campaign_id)
            contact = db.scalar(select(Contact).where(Contact.company_id == company.id).order_by(Contact.confidence_score.desc()))
            if not company.analyzed_at or not contact:
                continue
            mapping = db.get(ServiceMapping, company.industry.casefold())
            service = campaign.service_override or (mapping.service if mapping else DEFAULT_SERVICES.get(company.industry.casefold(), ''))
            lead.contact_id = contact.id if contact else None
            lead.service = service
            lead.score = score_lead(company, contact, campaign, service)
            lead.state = 'qualified' if contact and lead.score >= max(campaign.min_score, settings().min_lead_score) else 'rejected'
            db.add(AuditLog(action='LEAD_QUALIFIED', entity_type='campaign_lead', entity_id=lead.id, detail=str(lead.score)))
        db.commit()


def generate_emails():
    with SessionLocal() as db:
        for lead in db.scalars(select(CampaignLead).where(CampaignLead.state == 'qualified').limit(100)).all():
            if db.scalar(select(Email.id).where(Email.campaign_lead_id == lead.id, Email.sequence == 0)):
                continue
            company = db.get(Company, lead.company_id)
            subject, body = generate_with_llm(company, lead.service)
            db.add(Email(campaign_lead_id=lead.id, contact_id=lead.contact_id, subject=subject, body=body))
            db.add(AuditLog(action='EMAIL_GENERATED', entity_type='campaign_lead', entity_id=lead.id))
        db.commit()


def send_approved_emails():
    with SessionLocal() as db:
        ids = db.scalars(select(Email.id).where(Email.status == 'draft').order_by(Email.id).limit(25)).all()
        for email_id in ids:
            send_one(db, email_id)


def run_pipeline():
    if not settings().enable_automation:
        return
    for task in (discover_leads, analyze_websites, qualify_leads, generate_emails):
        try:
            task()
        except Exception as exc:
            with SessionLocal() as db:
                db.add(AuditLog(action='JOB_FAILED', detail=f'{task.__name__}: {type(exc).__name__}'))
                db.commit()
    with SessionLocal() as db:
        try:
            poll_inbox(db)
            schedule_followups(db)
        except Exception as exc:
            db.rollback()
            db.add(AuditLog(action='JOB_FAILED', detail=f'inbox_or_followup: {type(exc).__name__}'))
            db.commit()
    try:
        send_approved_emails()
    except Exception as exc:
        with SessionLocal() as db:
            db.add(AuditLog(action='JOB_FAILED', detail=f'send: {type(exc).__name__}'))
            db.commit()
