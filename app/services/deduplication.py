"""Duplicate detection across companies, domains, emails and outreach history."""
import re
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CampaignLead, Company, Contact, Email, Suppression

_EMAIL_RE = re.compile(r'^[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}$')


def domain_of(value: str) -> str:
    """Normalise a URL (or bare host) to a plain, validated hostname."""
    host = urlsplit(value if '://' in value else 'https://' + value).hostname or ''
    host = host.lower().removeprefix('www.')
    if not host or not re.fullmatch(r'[a-z0-9.-]+', host) or '.' not in host:
        raise ValueError('Invalid public domain')
    return host


def name_key(value: str) -> str:
    """Normalise a company name for comparison (case/punctuation-insensitive)."""
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def normalize_email(value: str) -> str:
    return value.strip().lower()


def valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(value)))


def find_company(db: Session, name: str, website: str, city: str = '') -> Company | None:
    """Find an existing company by domain OR by name+city key."""
    key = name_key(name) + ':' + name_key(city)
    return db.scalar(select(Company).where(or_(Company.domain == domain_of(website), Company.name_key == key)))


def is_suppressed(db: Session, email: str) -> bool:
    """True when the address itself or its whole domain is on the suppression list."""
    email = normalize_email(email)
    domain = email.partition('@')[2]
    return bool(db.scalar(select(Suppression.id).where(or_(Suppression.email == email, Suppression.domain == domain))))


def suppress(db: Session, email: str, reason: str) -> None:
    """Add an address to the suppression list (idempotent)."""
    email = normalize_email(email)
    if email and not db.scalar(select(Suppression.id).where(Suppression.email == email)):
        db.add(Suppression(email=email, reason=reason[:100]))


def suppress_domain(db: Session, domain: str, reason: str) -> None:
    domain = domain_of(domain)
    if not db.scalar(select(Suppression.id).where(Suppression.domain == domain)):
        db.add(Suppression(domain=domain, reason=reason[:100]))


def already_contacted(db: Session, company_id: int, email: str) -> bool:
    """True when the company or the address has an email that was (trying to be)
    sent — including uncertain outcomes that must never be auto-retried."""
    transmitted = {
        'status': ('sending', 'sent'),
        'outcome': ('sent', 'uncertain'),
    }
    return bool(
        db.scalar(
            select(Email.id)
            .join(CampaignLead, Email.campaign_lead_id == CampaignLead.id)
            .join(Contact, Email.contact_id == Contact.id)
            .where(
                or_(CampaignLead.company_id == company_id, Contact.email == normalize_email(email)),
                or_(Email.status.in_(transmitted['status']), Email.outcome.in_(transmitted['outcome'])),
            )            .limit(1)
        )
    )
