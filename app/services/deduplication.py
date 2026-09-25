import re
from urllib.parse import urlsplit
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.models import Company, Contact, Suppression, CampaignLead, Email


def domain_of(value: str) -> str:
    host = urlsplit(value if '://' in value else 'https://' + value).hostname or ''
    host = host.lower().removeprefix('www.')
    if not host or not re.fullmatch(r'[a-z0-9.-]+', host) or '.' not in host:
        raise ValueError('Invalid public domain')
    return host


def name_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def normalize_email(value: str) -> str:
    return value.strip().lower()


def find_company(db: Session, name: str, website: str, city: str = '') -> Company | None:
    key = name_key(name) + ':' + name_key(city)
    return db.scalar(select(Company).where(or_(Company.domain == domain_of(website), Company.name_key == key)))


def is_suppressed(db: Session, email: str) -> bool:
    email = normalize_email(email)
    domain = email.partition('@')[2]
    return bool(db.scalar(select(Suppression.id).where(or_(Suppression.email == email, Suppression.domain == domain))))


def already_contacted(db: Session, company_id: int, email: str) -> bool:
    return bool(db.scalar(select(Email.id).join(CampaignLead, Email.campaign_lead_id == CampaignLead.id).join(Contact, Email.contact_id == Contact.id).where(Email.status.in_(['sending', 'sent', 'uncertain']), or_(CampaignLead.company_id == company_id, Contact.email == normalize_email(email))).limit(1)))
