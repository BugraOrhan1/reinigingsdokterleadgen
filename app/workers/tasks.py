"""Idempotent pipeline tasks run by the scheduler / workforce.

Every task opens its own session, is safe to re-run and commits only what it
changed. Sending is the only side-effecting step with an external outcome and
is therefore gated by tasks/mailer exactly-once semantics.
"""
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, Reply, ServiceMapping, Setting, Suppression, now
from app.services.contact_finder import find_contacts
from app.services.deduplication import domain_of, find_company, name_key
from app.services.email_generator import generate_with_llm
from app.services.followup import schedule_followups
from app.services.inbox_polling import poll_inbox
from app.services.lead_finder import discover
from app.services.lead_qualifier import DEFAULT_SERVICES, score_lead
from app.services.mailer import send_one
from app.services.website_analyzer import extract_facts, fetch_pages


def log(db, action: str, entity_type: str = '', entity_id: int | None = None, detail: str = '') -> None:
    db.add(AuditLog(action=action[:50], entity_type=entity_type[:50], entity_id=entity_id, detail=detail[:500]))


def add_company(db, data: dict) -> Company:
    """Insert a discovered company unless duplicates already exist."""
    existing = find_company(db, data['company_name'], data['website'], data.get('city', ''))
    if existing:
        return existing
    company = Company(
        **data,
        domain=domain_of(data['website']),
        name_key=name_key(data['company_name']) + ':' + name_key(data.get('city', '')),
    )
    db.add(company)
    db.flush()
    log(db, 'LEAD_FOUND', 'company', company.id, company.source)
    return company


def _cached_stamp(db, key: str) -> bool:
    stamp = db.get(Setting, key)
    return bool(stamp and stamp.value.get('date') == now().date().isoformat())


def _stamp(db, key: str) -> None:
    db.merge(Setting(key=key, value={'date': now().date().isoformat()}))


def discover_leads() -> int:
    """Discover businesses for active campaigns (at most once per campaign/day)."""
    found = 0
    with SessionLocal() as db:
        for campaign in db.scalars(select(Campaign).where(Campaign.active)).all():
            stamp_key = f'discovery_last_{campaign.id}'
            if _cached_stamp(db, stamp_key):
                continue
            try:
                for industry in campaign.industries:
                    for data in discover(industry, campaign.region, campaign.city, campaign.radius_km):
                        company = add_company(db, data)
                        if not db.scalar(select(CampaignLead.id).where(CampaignLead.campaign_id == campaign.id, CampaignLead.company_id == company.id)):
                            db.add(CampaignLead(campaign_id=campaign.id, company_id=company.id))
                        found += 1
                _stamp(db, stamp_key)
                db.commit()
            except Exception as exc:  # noqa: BLE001 — per-campaign isolation
                db.rollback()
                log(db, 'JOB_FAILED', 'campaign', campaign.id, f'discover: {type(exc).__name__}')
                db.commit()
    return found


def analyze_websites() -> int:
    """Analyse public pages of companies that were never analysed."""
    done = 0
    with SessionLocal() as db:
        companies = db.scalars(
            select(Company).where(Company.analyzed_at.is_(None), Company.blocked.is_(False)).limit(50)
        ).all()
        for company in companies:
            try:
                pages = fetch_pages(company.website)
                company.facts = extract_facts(pages)
                company.analyzed_at = now()
                log(db, 'WEBSITE_ANALYZED', 'company', company.id)
                db.commit()
                done += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log(db, 'JOB_FAILED', 'company', company.id, f'analyze: {type(exc).__name__}')
                try:
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
    return done


def find_contacts_task() -> int:
    """Discover general business contacts for analysed companies that have none yet."""
    done = 0
    with SessionLocal() as db:
        companies = db.scalars(
            select(Company)
            .where(Company.analyzed_at.is_not(None), Company.blocked.is_(False))
            .limit(100)
        ).all()
        for company in companies:
            if db.scalar(select(Contact.id).where(Contact.company_id == company.id)):
                continue
            try:
                pages = fetch_pages(company.website)
                for item in find_contacts(pages, company.website):
                    if not db.scalar(select(Contact.id).where(Contact.email == item['email'])):
                        db.add(Contact(company_id=company.id, **item))
                        log(db, 'CONTACT_FOUND', 'company', company.id, item['email'])
                db.commit()
                done += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log(db, 'JOB_FAILED', 'company', company.id, f'find_contacts: {type(exc).__name__}')
                try:
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
    return done


def qualify_leads() -> int:
    """Score new leads and link their best-known contact + service."""
    done = 0
    with SessionLocal() as db:
        leads = db.scalars(select(CampaignLead).where(CampaignLead.state == 'new').limit(100)).all()
        for lead in leads:
            company = db.get(Company, lead.company_id)
            campaign = db.get(Campaign, lead.campaign_id)
            if not company or not campaign:
                continue
            contact = db.scalar(select(Contact).where(Contact.company_id == company.id).order_by(Contact.confidence_score.desc()))
            mapping = db.get(ServiceMapping, company.industry.casefold())
            service = campaign.service_override or (mapping.service if mapping else DEFAULT_SERVICES.get(company.industry.casefold(), ''))
            lead.contact_id = contact.id if contact else None
            lead.service = service
            lead.score = score_lead(company, contact, campaign, service)
            threshold = max(campaign.min_score, settings().min_lead_score)
            lead.state = 'qualified' if contact and lead.score >= threshold else 'rejected'
            log(db, 'LEAD_QUALIFIED', 'campaign_lead', lead.id, str(lead.score))
            done += 1
        db.commit()
    return done


def generate_emails() -> int:
    """Draft the initial email for every qualified lead (idempotent)."""
    done = 0
    with SessionLocal() as db:
        leads = db.scalars(select(CampaignLead).where(CampaignLead.state == 'qualified').limit(100)).all()
        for lead in leads:
            if db.scalar(select(Email.id).where(Email.campaign_lead_id == lead.id, Email.sequence == 0)):
                continue
            company = db.get(Company, lead.company_id)
            if not company:
                continue
            subject, body = generate_with_llm(company, lead.service)
            db.add(Email(campaign_lead_id=lead.id, contact_id=lead.contact_id, subject=subject, body=body))
            log(db, 'EMAIL_GENERATED', 'campaign_lead', lead.id)
            done += 1
        db.commit()
    return done


def send_approved_emails() -> int:
    """Send the next batch of drafts through the hardened gated sender."""
    sent = 0
    with SessionLocal() as db:
        ids = db.scalars(select(Email.id).where(Email.status == 'draft').order_by(Email.id).limit(25)).all()
        for email_id in ids:
            outcome = send_one(db, email_id)
            if outcome in ('sent', 'dry_run', 'uncertain'):
                sent += 1
    return sent


def process_inbox() -> int:
    """Read inbound replies/bounces from IMAP (if configured)."""
    with SessionLocal() as db:
        return poll_inbox(db)


def process_bounces() -> int:
    """Reconcile bounce handling idempotently: every BOUNCE reply must have its
    target address suppressed and its leads stopped, even if an earlier run
    failed partway. Then re-scan the IMAP inbox for new bounce messages."""
    fixed = 0
    with SessionLocal() as db:
        for reply in db.scalars(select(Reply).where(Reply.classification == 'BOUNCE')).all():
            contact = db.get(Contact, reply.contact_id) if reply.contact_id else None
            address = contact.email if contact else ''
            if address:
                if not db.scalar(select(Suppression.id).where(Suppression.email == address)):
                    db.add(Suppression(email=address, reason='bounce'))
                changed = 0
                for lead in db.scalars(select(CampaignLead).where(CampaignLead.contact_id == contact.id)).all():
                    if lead.state != 'bounced':
                        lead.state = 'bounced'
                        changed += 1
                if changed:
                    fixed += changed
            db.commit()
        # Also re-read any inbound bounce messages not yet processed.
        inbox = poll_inbox(db)
    return fixed + inbox


def schedule_followups_task() -> int:
    with SessionLocal() as db:
        return schedule_followups(db)


def update_statistics() -> int:
    """Refresh derived counters (kept lightweight)."""
    with SessionLocal() as db:
        log(db, 'STATISTICS_UPDATED')
        db.commit()
    return 1


PIPELINE = (discover_leads, analyze_websites, find_contacts_task, qualify_leads, generate_emails)


def run_pipeline() -> dict:
    """Orchestrate one pipeline pass. Every task failure is logged separately."""
    if not settings().enable_automation:
        return {'status': 'automation_disabled'}
    results: dict[str, str | int] = {}
    for task in PIPELINE:
        try:
            results[task.__name__] = task()
        except Exception as exc:  # noqa: BLE001
            results[task.__name__] = 'failed'
            with SessionLocal() as db:
                log(db, 'JOB_FAILED', detail=f'{task.__name__}: {type(exc).__name__}')
                db.commit()
    for task in (process_inbox, schedule_followups_task, send_approved_emails, update_statistics):
        try:
            results[task.__name__] = task()
        except Exception as exc:  # noqa: BLE001
            results[task.__name__] = 'failed'
            with SessionLocal() as db:
                log(db, 'JOB_FAILED', detail=f'{task.__name__}: {type(exc).__name__}')
                db.commit()
    return results
