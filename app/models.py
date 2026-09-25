from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def now():
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = 'companies'
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    name_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    website: Mapped[str] = mapped_column(String(2048))
    industry: Mapped[str] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100), default='')
    region: Mapped[str] = mapped_column(String(100), default='')
    address: Mapped[str] = mapped_column(String(500), default='')
    source: Mapped[str] = mapped_column(String(100), default='manual')
    source_url: Mapped[str] = mapped_column(String(2048), default='')
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    contacts: Mapped[list['Contact']] = relationship(back_populates='company')


class Contact(Base):
    __tablename__ = 'contacts'
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    email_source_url: Mapped[str] = mapped_column(String(2048))
    contact_type: Mapped[str] = mapped_column(String(50), default='general')
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    verification_status: Mapped[str] = mapped_column(String(30), default='publicly_listed')
    company: Mapped[Company] = relationship(back_populates='contacts')


class Campaign(Base):
    __tablename__ = 'campaigns'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    industries: Mapped[list] = mapped_column(JSON, default=list)
    region: Mapped[str] = mapped_column(String(100), default='')
    city: Mapped[str] = mapped_column(String(100), default='')
    radius_km: Mapped[int] = mapped_column(Integer, default=0)
    daily_limit: Mapped[int] = mapped_column(Integer, default=25)
    min_score: Mapped[int] = mapped_column(Integer, default=65)
    service_override: Mapped[str] = mapped_column(String(200), default='')
    max_followups: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CampaignLead(Base):
    __tablename__ = 'campaign_leads'
    __table_args__ = (UniqueConstraint('campaign_id', 'company_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey('campaigns.id'), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey('contacts.id'))
    score: Mapped[int] = mapped_column(Integer, default=0)
    service: Mapped[str] = mapped_column(String(200), default='')
    state: Mapped[str] = mapped_column(String(30), default='new')
    followups_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Email(Base):
    __tablename__ = 'emails'
    __table_args__ = (UniqueConstraint('campaign_lead_id', 'sequence'), Index('ix_emails_status_created', 'status', 'created_at'))
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_lead_id: Mapped[int] = mapped_column(ForeignKey('campaign_leads.id'), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey('contacts.id'))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='draft')
    provider_id: Mapped[str] = mapped_column(String(255), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailEvent(Base):
    __tablename__ = 'email_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey('emails.id'), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    detail: Mapped[str] = mapped_column(String(500), default='')


class Reply(Base):
    __tablename__ = 'replies'
    __table_args__ = (UniqueConstraint('external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey('contacts.id'))
    email_id: Mapped[int | None] = mapped_column(ForeignKey('emails.id'))
    classification: Mapped[str] = mapped_column(String(30))
    snippet: Mapped[str] = mapped_column(String(500), default='')
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Suppression(Base):
    __tablename__ = 'suppression_list'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    reason: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), default='')
    entity_id: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class ServiceMapping(Base):
    __tablename__ = 'service_mappings'
    industry: Mapped[str] = mapped_column(String(100), primary_key=True)
    service: Mapped[str] = mapped_column(String(200))
