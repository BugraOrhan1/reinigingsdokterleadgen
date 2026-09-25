from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import AuditLog, CampaignLead, Contact, Email, EmailEvent, Reply, Suppression
from app.services.deduplication import normalize_email


def classify(subject: str, body: str) -> str:
    text = (subject + ' ' + body).casefold()
    if any(word in text for word in ('afmelden', 'unsubscribe', 'verwijder mij', 'geen mails meer')):
        return 'UNSUBSCRIBE'
    if any(word in text for word in ('undeliverable', 'delivery failed', 'mail delivery subsystem')):
        return 'BOUNCE'
    if any(word in text for word in ('out of office', 'afwezig', 'autoreply')):
        return 'OUT_OF_OFFICE'
    if any(word in text for word in ('geen interesse', 'niet geïnteresseerd', 'not interested')):
        return 'NOT_INTERESTED'
    if any(word in text for word in ('interesse', 'graag contact', 'bel mij')):
        return 'INTERESTED'
    if '?' in text:
        return 'QUESTION'
    return 'OTHER'


def process_reply(db: Session, external_id: str, sender: str, subject: str, body: str, forced_classification: str = '', target_email: str = '') -> str:
    if db.scalar(select(Reply.id).where(Reply.external_id == external_id)):
        return 'duplicate'
    sender = normalize_email(sender)
    kind = forced_classification or classify(subject, body)
    if kind not in ('INTERESTED', 'NOT_INTERESTED', 'QUESTION', 'OUT_OF_OFFICE', 'UNSUBSCRIBE', 'BOUNCE', 'OTHER'):
        raise ValueError('Invalid classification')
    address = normalize_email(target_email) if kind == 'BOUNCE' and target_email else sender
    contact = db.scalar(select(Contact).where(Contact.email == address))
    reply = Reply(external_id=external_id, contact_id=contact.id if contact else None, classification=kind, snippet=body[:500])
    db.add(reply)
    if kind == 'UNSUBSCRIBE' and not db.scalar(select(Suppression.id).where(Suppression.email == address)):
        db.add(Suppression(email=address, reason='unsubscribe'))
    if contact:
        if kind == 'UNSUBSCRIBE':
            domain = address.partition('@')[2]
            if not db.scalar(select(Suppression.id).where(Suppression.domain == domain)):
                db.add(Suppression(domain=domain, reason='company_unsubscribe'))
        leads = db.scalars(select(CampaignLead).where(CampaignLead.contact_id == contact.id)).all()
        if kind == 'BOUNCE':
            if not db.scalar(select(Suppression.id).where(Suppression.email == address)):
                db.add(Suppression(email=address, reason=kind.lower()))
        for lead in leads:
            if kind in ('UNSUBSCRIBE', 'BOUNCE', 'INTERESTED', 'NOT_INTERESTED', 'QUESTION', 'OTHER', 'OUT_OF_OFFICE'):
                lead.state = {'UNSUBSCRIBE': 'unsubscribed', 'BOUNCE': 'bounced'}.get(kind, 'replied')
            for draft in db.scalars(select(Email).where(Email.campaign_lead_id == lead.id, Email.status == 'draft')):
                if lead.state != 'new':
                    draft.status = 'cancelled'
    db.add(AuditLog(action={'UNSUBSCRIBE': 'OPT_OUT', 'BOUNCE': 'EMAIL_BOUNCED'}.get(kind, 'REPLY_RECEIVED'), entity_type='contact', entity_id=contact.id if contact else None, detail=kind))
    db.commit()
    return kind
