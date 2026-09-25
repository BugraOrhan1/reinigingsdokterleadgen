"""FastAPI dashboard + JSON API for the Reinigingsdokter lead system."""
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    AuditLog,
    Campaign,
    CampaignLead,
    Company,
    Contact,
    Email,
    EmailEvent,
    Reply,
    ServiceMapping,
    Suppression,
)
from app.services.deduplication import domain_of, normalize_email
from app.services.inbox_processor import VALID_CLASSIFICATIONS, process_reply
from app.workers.tasks import add_company

STATIC_DIR = Path(__file__).parent / 'static'
SESSION_COOKIE = 'rd_session'
AUTH_EXEMPT_PATHS = {'/login', '/logout'}


def _sign(session_token: str) -> str:
    secret = settings().app_secret.encode()
    return hmac.new(secret, session_token.encode(), 'sha256').hexdigest()


def _make_token() -> str:
    token = secrets.token_urlsafe(32)
    return f'{token}.{_sign(token)}'


def _verify_token(cookie_value: str) -> bool:
    if not cookie_value or '.' not in cookie_value:
        return False
    token, signature = cookie_value.split('.', 1)
    return hmac.compare_digest(_sign(token), signature)


app = FastAPI(title='Reinigingsdokter Outreach', docs_url=None, redoc_url=None)
rate_buckets: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else 'unknown'
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    recent = rate_buckets[ip]
    while recent and recent[0] < cutoff:
        recent.popleft()
    if len(recent) >= 120:
        raise HTTPException(429, 'Too many requests')
    recent.append(datetime.now(timezone.utc))


def auth(request: Request, rd_session: str | None = Cookie(default=None)) -> None:
    """Session-cookie authentication with a login redirect for browsers."""
    _rate_limit(request)
    if request.url.path in AUTH_EXEMPT_PATHS:
        return
    cfg = settings()
    if not rd_session or not _verify_token(rd_session):
        if request.headers.get('accept', '').startswith('text/html'):
            raise HTTPException(303, headers={'Location': '/login'})
        raise HTTPException(401, 'Unauthorized')
    if cfg.insecure_defaults:
        raise HTTPException(503, 'Default credentials detected — configure APP_SECRET and ADMIN_PASSWORD in .env before use')
    # Origin check for state-changing requests (CSRF defence for cookie auth).
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Invalid origin')


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    _rate_limit(request)
    html = STATIC_DIR.joinpath('login.html')
    if html.exists():
        return html.read_text(encoding='utf-8')
    return '<html><body><form method="post" action="/login"><input name="username"><input name="password" type="password"><button>Login</button></form></body></html>'


@app.post('/login')
def login(username: str = Form(''), password: str = Form('')):
    cfg = settings()
    if cfg.insecure_defaults:
        raise HTTPException(503, 'Default credentials — configure APP_SECRET and ADMIN_PASSWORD first')
    if not (hmac.compare_digest(username, cfg.admin_username) and hmac.compare_digest(password, cfg.admin_password)):
        raise HTTPException(401, 'Ongeldige inloggegevens')
    response = RedirectResponse('/', status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        _make_token(),
        max_age=settings().session_max_age_hours * 3600,
        httponly=True,
        samesite='strict',
        secure=settings().cookie_secure,
    )
    return response


@app.post('/logout')
def logout():
    response = RedirectResponse('/login', status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------- schemas
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
    radius_km: int = Field(default=0, ge=0, le=250)
    daily_limit: int = Field(default=25, ge=1, le=500)
    min_score: int = Field(default=65, ge=0, le=100)
    service_override: str = ''
    max_followups: int = Field(default=1, ge=0, le=5)


class ReplyIn(BaseModel):
    external_id: str
    sender: str
    subject: str = ''
    body: str = ''
    classification: str = ''
    target_email: str = ''


# ---------------------------------------------------------------- pages
@app.get('/', response_class=HTMLResponse, dependencies=[Depends(auth)])
def dashboard():
    return STATIC_DIR.joinpath('index.html').read_text(encoding='utf-8')


# ---------------------------------------------------------------- overview
def _count(db: Session, model, where=None) -> int:
    query = select(func.count()).select_from(model)
    return db.scalar(query.where(where) if where is not None else query) or 0


@app.get('/api/overview', dependencies=[Depends(auth)])
def overview(db: Session = Depends(get_db)):
    return {
        'leads_found': _count(db, Company),
        'qualified_leads': _count(db, CampaignLead, CampaignLead.state.in_(['qualified', 'sent', 'replied', 'interested'])),
        'emails_generated': _count(db, Email),
        'emails_sent': _count(db, Email, Email.status == 'sent'),
        'delivered': _count(db, EmailEvent, EmailEvent.event_type == 'delivered'),
        'bounced': _count(db, Reply, Reply.classification == 'BOUNCE'),
        'replies': _count(db, Reply),
        'interested_replies': _count(db, Reply, Reply.classification == 'INTERESTED'),
        'opt_outs': _count(db, Reply, Reply.classification == 'UNSUBSCRIBE'),
        'dry_run': settings().dry_run,
        'live_send_enabled': settings().live_send_enabled,
        'automation': settings().enable_automation,
    }


# ---------------------------------------------------------------- leads
def _company_rows(db: Session):
    return db.scalars(select(Company).order_by(Company.id.desc()).limit(500)).all()


@app.get('/api/leads', dependencies=[Depends(auth)])
def leads(db: Session = Depends(get_db)):
    return [
        {
            'id': x.id, 'company_name': x.company_name, 'website': x.website,
            'industry': x.industry, 'city': x.city, 'region': x.region,
            'blocked': x.blocked, 'source': x.source, 'analyzed': x.analyzed_at is not None,
        }
        for x in _company_rows(db)
    ]


@app.post('/api/leads', dependencies=[Depends(auth)])
def create_lead(payload: CompanyIn, db: Session = Depends(get_db)):
    try:
        domain_of(str(payload.website))
        company = add_company(db, {**payload.model_dump(exclude={'website'}), 'website': str(payload.website), 'source': 'manual', 'source_url': str(payload.website)})
        db.commit()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'id': company.id}


@app.post('/api/leads/{company_id}/block', dependencies=[Depends(auth)])
def block_lead(company_id: int, blocked: bool = True, db: Session = Depends(get_db)):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404)
    company.blocked = blocked
    db.add(AuditLog(action='LEAD_BLOCKED' if blocked else 'LEAD_UNBLOCKED', entity_type='company', entity_id=company_id))
    db.commit()
    return {'blocked': company.blocked}


# ---------------------------------------------------------------- campaigns
@app.get('/api/campaigns', dependencies=[Depends(auth)])
def campaigns(db: Session = Depends(get_db)):
    rows = db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()
    result = []
    for c in rows:
        lead_count = _count(db, CampaignLead, CampaignLead.campaign_id == c.id)
        result.append({
            'id': c.id, 'name': c.name, 'industries': c.industries, 'region': c.region,
            'city': c.city, 'radius_km': c.radius_km, 'active': c.active,
            'daily_limit': c.daily_limit, 'min_score': c.min_score,
            'max_followups': c.max_followups, 'service_override': c.service_override,
            'lead_count': lead_count,
        })
    return result


@app.get('/api/campaigns/{campaign_id}/leads', dependencies=[Depends(auth)])
def campaign_leads(campaign_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(CampaignLead, Company)
        .join(Company, Company.id == CampaignLead.company_id)
        .where(CampaignLead.campaign_id == campaign_id)
        .order_by(CampaignLead.id.desc())
    ).all()
    return [
        {
            'id': lead.id, 'company_id': company.id, 'company_name': company.company_name,
            'score': lead.score, 'service': lead.service, 'state': lead.state,
            'followups_sent': lead.followups_sent,
        }
        for lead, company in rows
    ]


@app.post('/api/campaigns', dependencies=[Depends(auth)])
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db)):
    data = payload.model_dump()
    data['industries'] = [x.strip() for x in data['industries'] if x.strip()]
    if not data['industries']:
        raise HTTPException(422, 'Ten minste één branche is verplicht')
    campaign = Campaign(**data)
    db.add(campaign)
    db.add(AuditLog(action='CAMPAIGN_CREATED', entity_type='campaign', detail=payload.name[:200]))
    db.commit()
    return {'id': campaign.id}


@app.delete('/api/campaigns/{campaign_id}', dependencies=[Depends(auth)])
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    db.add(AuditLog(action='CAMPAIGN_DELETED', entity_type='campaign', entity_id=campaign_id))
    db.delete(campaign)
    db.commit()
    return {'deleted': campaign_id}


@app.post('/api/campaigns/{campaign_id}/status', dependencies=[Depends(auth)])
def set_campaign_status(campaign_id: int, active: bool, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404)
    campaign.active = active
    db.add(AuditLog(action='CAMPAIGN_STARTED' if active else 'CAMPAIGN_PAUSED', entity_type='campaign', entity_id=campaign_id))
    db.commit()
    return {'active': active}


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


# ---------------------------------------------------------------- emails
@app.get('/api/emails', dependencies=[Depends(auth)])
def emails(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Email, Company)
        .join(CampaignLead, Email.campaign_lead_id == CampaignLead.id)
        .join(Company, CampaignLead.company_id == Company.id)
        .order_by(Email.id.desc())
        .limit(300)
    ).all()
    return [
        {
            'id': e.id, 'company_name': c.company_name, 'subject': e.subject, 'body': e.body,
            'status': e.status, 'outcome': e.outcome, 'sequence': e.sequence,
            'created_at': e.created_at, 'sent_at': e.sent_at, 'error': e.error,
        }
        for e, c in rows
    ]


# ---------------------------------------------------------------- replies
@app.get('/api/replies', dependencies=[Depends(auth)])
def replies(db: Session = Depends(get_db)):
    return [
        {'id': x.id, 'classification': x.classification, 'snippet': x.snippet, 'received_at': x.received_at}
        for x in db.scalars(select(Reply).order_by(Reply.id.desc()).limit(300))
    ]


@app.post('/api/replies', dependencies=[Depends(auth)])
def ingest_reply(payload: ReplyIn, db: Session = Depends(get_db)):
    if payload.classification and payload.classification not in VALID_CLASSIFICATIONS:
        raise HTTPException(422, 'Ongeldige classificatie')
    kind = process_reply(
        db,
        external_id=payload.external_id,
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        forced_classification=payload.classification,
        target_email=payload.target_email,
    )
    return {'classification': kind}


# ---------------------------------------------------------------- interested
@app.get('/api/interested', dependencies=[Depends(auth)])
def interested(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Company.company_name, Contact.email, Reply.snippet)
        .join(Contact, Contact.company_id == Company.id)
        .join(Reply, Reply.contact_id == Contact.id)
        .where(Reply.classification == 'INTERESTED')
        .order_by(Reply.id.desc())
    ).all()
    return [{'company_name': n, 'email': e, 'snippet': s} for n, e, s in rows]


# ---------------------------------------------------------------- suppression
@app.get('/api/suppression', dependencies=[Depends(auth)])
def suppression(db: Session = Depends(get_db)):
    return [
        {'id': x.id, 'email': x.email, 'domain': x.domain, 'reason': x.reason, 'created_at': x.created_at}
        for x in db.scalars(select(Suppression).order_by(Suppression.id.desc()).limit(300))
    ]


@app.post('/api/suppression', dependencies=[Depends(auth)])
def add_suppression(email: str = '', domain: str = '', db: Session = Depends(get_db)):
    if not email and not domain:
        raise HTTPException(422, 'Email of domein is verplicht')
    item = Suppression(email=normalize_email(email) if email else None, domain=domain_of(domain) if domain else None, reason='manual')
    db.add(item)
    db.add(AuditLog(action='OPT_OUT', detail='manual'))
    db.commit()
    return {'id': item.id}


@app.delete('/api/suppression/{item_id}', dependencies=[Depends(auth)])
def remove_suppression(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Suppression, item_id)
    if not item:
        raise HTTPException(404)
    db.add(AuditLog(action='OPT_OUT_REMOVED', detail=item.email or item.domain or ''))
    db.delete(item)
    db.commit()
    return {'deleted': item_id}


# ---------------------------------------------------------------- settings
@app.get('/api/settings', dependencies=[Depends(auth)])
def get_settings(db: Session = Depends(get_db)):
    cfg = settings()
    return {
        'configuration': {
            'dry_run': cfg.dry_run,
            'live_send_enabled': cfg.live_send_enabled,
            'enable_automation': cfg.enable_automation,
            'daily_email_limit': cfg.daily_email_limit,
            'hourly_email_limit': cfg.hourly_email_limit,
            'min_delay_seconds': cfg.min_delay_seconds,
            'max_delay_seconds': cfg.max_delay_seconds,
            'min_lead_score': cfg.min_lead_score,
            'max_followups': cfg.max_followups,
            'followup_delay_days': cfg.followup_delay_days,
            'service_regions': cfg.service_regions,
            'company_name': cfg.company_name,
            'company_email': cfg.company_email,
            'company_website': cfg.company_website,
        },
        'service_mappings': [
            {'industry': x.industry, 'service': x.service}
            for x in db.scalars(select(ServiceMapping).order_by(ServiceMapping.industry))
        ],
    }


@app.put('/api/settings/services/{industry}', dependencies=[Depends(auth)])
def set_service(industry: str, service: str, db: Session = Depends(get_db)):
    if not service or len(service) > 200:
        raise HTTPException(422)
    if not industry or len(industry) > 100:
        raise HTTPException(422)
    key = industry.casefold()
    item = db.get(ServiceMapping, key) or ServiceMapping(industry=key, service=service)
    item.service = service
    db.add(item)
    db.add(AuditLog(action='SETTING_CHANGED', entity_type='service', detail=key[:100]))
    db.commit()
    return {'industry': item.industry, 'service': item.service}


# ---------------------------------------------------------------- logs
@app.get('/api/logs', dependencies=[Depends(auth)])
def logs(limit: int = 300, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 1000)
    return [
        {'action': x.action, 'entity_type': x.entity_type, 'entity_id': x.entity_id, 'detail': x.detail, 'created_at': x.created_at}
        for x in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    ]
