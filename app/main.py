import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import AuditLog, Campaign, CampaignLead, Company, Contact, Email, EmailEvent, Reply, ServiceMapping, Setting, Suppression
from app.services.deduplication import domain_of, is_suppressed, normalize_email
from app.services.inbox_processor import process_reply
from app.workers.tasks import add_company

app = FastAPI(title='Reinigingsdokter Outreach', docs_url=None, redoc_url=None)
security = HTTPBasic()
attempts = defaultdict(deque)


def auth(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    ip = request.client.host if request.client else 'unknown'
    now = datetime.now(timezone.utc)
    recent = attempts[ip]
    while recent and recent[0] < now - timedelta(minutes=5):
        recent.popleft()
    if len(recent) >= 20:
        raise HTTPException(429, 'Too many requests')
    cfg = settings()
    if cfg.admin_password == 'change-me' or cfg.app_secret == 'change-me' or not (secrets.compare_digest(credentials.username, cfg.admin_username) and secrets.compare_digest(credentials.password, cfg.admin_password)):
        recent.append(now)
        raise HTTPException(401, 'Unauthorized', headers={'WWW-Authenticate': 'Basic'})
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Invalid origin')


class CompanyIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    website: HttpUrl
    industry: str = Field(min_length=2, max_length=100)
    city: str = ''
    region: str = ''
    address: str = ''


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    industries: list[str] = Field(min_length=1)
    region: str = ''
    city: str = ''
    daily_limit: int = Field(default=25, ge=1, le=100)
    min_score: int = Field(default=65, ge=0, le=100)
    service_override: str = ''
    max_followups: int = Field(default=1, ge=0, le=3)


class ReplyIn(BaseModel):
    external_id: str
    sender: str
    subject: str = ''
    body: str = ''
    classification: str = ''
    target_email: str = ''


@app.get('/', response_class=HTMLResponse, dependencies=[Depends(auth)])
def dashboard():
    from pathlib import Path
    return Path(__file__).parent.joinpath('static', 'index.html').read_text(encoding='utf-8')


@app.get('/api/overview', dependencies=[Depends(auth)])
def overview(db: Session = Depends(get_db)):
    def count(model, where=None):
        query = select(func.count()).select_from(model)
        return db.scalar(query.where(where) if where is not None else query) or 0
    return {'leads_found': count(Company), 'qualified_leads': count(CampaignLead, CampaignLead.state.in_(['qualified', 'sent', 'replied'])), 'emails_generated': count(Email), 'emails_sent': count(Email, Email.status == 'sent'), 'delivered': count(EmailEvent, EmailEvent.event_type == 'delivered'), 'bounced': count(Reply, Reply.classification == 'BOUNCE'), 'replies': count(Reply), 'interested_replies': count(Reply, Reply.classification == 'INTERESTED'), 'opt_outs': count(Reply, Reply.classification == 'UNSUBSCRIBE'), 'dry_run': settings().dry_run}


@app.get('/api/leads', dependencies=[Depends(auth)])
def leads(db: Session = Depends(get_db)):
    return [{'id': x.id, 'company_name': x.company_name, 'website': x.website, 'industry': x.industry, 'city': x.city, 'region': x.region, 'blocked': x.blocked} for x in db.scalars(select(Company).order_by(Company.id.desc()).limit(200))]


@app.post('/api/leads', dependencies=[Depends(auth)])
def create_lead(payload: CompanyIn, db: Session = Depends(get_db)):
    try:
        domain_of(str(payload.website))
        company = add_company(db, {**payload.model_dump(exclude={'website'}), 'website': str(payload.website), 'source': 'manual', 'source_url': str(payload.website)})
        db.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'id': company.id}


@app.post('/api/campaigns/{campaign_id}/leads/{company_id}', dependencies=[Depends(auth)])
def attach_lead(campaign_id: int, company_id: int, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id) or not db.get(Company, company_id):
        raise HTTPException(404)
    existing = db.scalar(select(CampaignLead).where(CampaignLead.campaign_id == campaign_id, CampaignLead.company_id == company_id))
    if existing:
        return {'id': existing.id}
    item = CampaignLead(campaign_id=campaign_id, company_id=company_id)
    db.add(item)
    db.commit()
    return {'id': item.id}


@app.get('/api/campaigns', dependencies=[Depends(auth)])
def campaigns(db: Session = Depends(get_db)):
    return [{'id': x.id, 'name': x.name, 'industries': x.industries, 'region': x.region, 'active': x.active, 'daily_limit': x.daily_limit, 'min_score': x.min_score, 'max_followups': x.max_followups} for x in db.scalars(select(Campaign).order_by(Campaign.id.desc()))]


@app.post('/api/campaigns', dependencies=[Depends(auth)])
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db)):
    campaign = Campaign(**payload.model_dump())
    db.add(campaign)
    db.commit()
    return {'id': campaign.id}


@app.post('/api/campaigns/{campaign_id}/status', dependencies=[Depends(auth)])
def set_campaign_status(campaign_id: int, active: bool, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    campaign.active = active
    db.add(AuditLog(action='CAMPAIGN_STARTED' if active else 'CAMPAIGN_PAUSED', entity_type='campaign', entity_id=campaign_id))
    db.commit()
    return {'active': active}


@app.get('/api/emails', dependencies=[Depends(auth)])
def emails(db: Session = Depends(get_db)):
    return [{'id': x.id, 'subject': x.subject, 'body': x.body, 'status': x.status, 'sequence': x.sequence, 'created_at': x.created_at} for x in db.scalars(select(Email).order_by(Email.id.desc()).limit(200))]


@app.get('/api/replies', dependencies=[Depends(auth)])
def replies(db: Session = Depends(get_db)):
    return [{'id': x.id, 'classification': x.classification, 'snippet': x.snippet, 'received_at': x.received_at} for x in db.scalars(select(Reply).order_by(Reply.id.desc()).limit(200))]


@app.post('/api/replies', dependencies=[Depends(auth)])
def ingest_reply(payload: ReplyIn, db: Session = Depends(get_db)):
    return {'classification': process_reply(db, **payload.model_dump(exclude={'classification'}), forced_classification=payload.classification)}


@app.get('/api/interested', dependencies=[Depends(auth)])
def interested(db: Session = Depends(get_db)):
    rows = db.execute(select(Company.company_name, Contact.email, Reply.snippet).join(Contact, Contact.company_id == Company.id).join(Reply, Reply.contact_id == Contact.id).where(Reply.classification == 'INTERESTED').order_by(Reply.id.desc())).all()
    return [{'company_name': n, 'email': e, 'snippet': s} for n, e, s in rows]


@app.get('/api/suppression', dependencies=[Depends(auth)])
def suppression(db: Session = Depends(get_db)):
    return [{'id': x.id, 'email': x.email, 'domain': x.domain, 'reason': x.reason} for x in db.scalars(select(Suppression).order_by(Suppression.id.desc()))]


@app.post('/api/suppression', dependencies=[Depends(auth)])
def add_suppression(email: str = '', domain: str = '', db: Session = Depends(get_db)):
    if not email and not domain:
        raise HTTPException(422, 'Email or domain required')
    item = Suppression(email=normalize_email(email) if email else None, domain=domain_of(domain) if domain else None, reason='manual')
    db.add(item)
    db.add(AuditLog(action='OPT_OUT', detail='manual'))
    db.commit()
    return {'id': item.id}


@app.get('/api/settings', dependencies=[Depends(auth)])
def get_settings(db: Session = Depends(get_db)):
    return {'configuration': {'dry_run': settings().dry_run, 'daily_email_limit': settings().daily_email_limit, 'hourly_email_limit': settings().hourly_email_limit, 'min_lead_score': settings().min_lead_score, 'max_followups': settings().max_followups}, 'service_mappings': [{'industry': x.industry, 'service': x.service} for x in db.scalars(select(ServiceMapping).order_by(ServiceMapping.industry))]}


@app.put('/api/settings/services/{industry}', dependencies=[Depends(auth)])
def set_service(industry: str, service: str, db: Session = Depends(get_db)):
    if not service or len(service) > 200:
        raise HTTPException(422)
    item = db.get(ServiceMapping, industry.casefold()) or ServiceMapping(industry=industry.casefold(), service=service)
    item.service = service
    db.add(item)
    db.add(AuditLog(action='SETTING_CHANGED', entity_type='service', detail=industry[:100]))
    db.commit()
    return {'industry': item.industry, 'service': item.service}


@app.get('/api/logs', dependencies=[Depends(auth)])
def logs(db: Session = Depends(get_db)):
    return [{'action': x.action, 'entity_type': x.entity_type, 'entity_id': x.entity_id, 'detail': x.detail, 'created_at': x.created_at} for x in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200))]
