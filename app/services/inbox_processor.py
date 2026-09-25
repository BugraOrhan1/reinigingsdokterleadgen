"""Classification and processing of inbound replies and bounces.

Behaviours:
  * UNSUBSCRIBE → address + domain go on the suppression list, autonomous
    mails stop forever;
  * BOUNCE → address suppressed and follow-ups stopped (never auto-resent);
  * INTERESTED / NOT_INTERESTED / QUESTION / OTHER → lead marked, follow-ups
    stopped, drafts cancelled whenever the reply definitely ends the thread;
  * OUT_OF_OFFICE is recorded but does not stop scheduled follow-ups.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, CampaignLead, Contact, Email, Reply
from app.services.deduplication import normalize_email, suppress, suppress_domain

VALID_CLASSIFICATIONS = ('INTERESTED', 'NOT_INTERESTED', 'QUESTION', 'OUT_OF_OFFICE', 'UNSUBSCRIBE', 'BOUNCE', 'OTHER')

# Outcomes that permanently stop automated outreach for that lead.
_STOP_STATES = {
    'UNSUBSCRIBE': 'unsubscribed',
    'BOUNCE': 'bounced',
    'INTERESTED': 'interested',
    'NOT_INTERESTED': 'not_interested',
    'QUESTION': 'replied',
    'OTHER': 'replied',
}


def classify(subject: str, body: str) -> str:
    text = (subject + ' ' + body).casefold()
    if any(word in text for word in ('afmelden', 'unsubscribe', 'opt-out', 'opt out', 'verwijder mij', 'geen mails meer', 'geen berichten meer')):
        return 'UNSUBSCRIBE'
    if any(word in text for word in ('undeliverable', 'delivery failed', 'delivery status notification', 'mail delivery subsystem', 'mail delivery failed', 'returned to sender')):
        return 'BOUNCE'
    if any(word in text for word in ('out of office', 'afwezig', 'autoreply', 'auto reply', 'automatisch antwoord')):
        return 'OUT_OF_OFFICE'
    if any(word in text for word in ('geen interesse', 'niet geïnteresseerd', 'niet geinteresseerd', 'not interested', 'geen behoefte')):
        return 'NOT_INTERESTED'
    if any(word in text for word in ('interesse', 'graag contact', 'bel mij', 'plan een afspraak', 'stuur meer info')):
        return 'INTERESTED'
    if '?' in text:
        return 'QUESTION'
    return 'OTHER'


def process_reply(db: Session, external_id: str, sender: str, subject: str = '', body: str = '', forced_classification: str = '', target_email: str = '') -> str:
    """Record and react to one inbound message. Idempotent per external_id."""
    if db.scalar(select(Reply.id).where(Reply.external_id == external_id)):
        return 'duplicate'
    sender = normalize_email(sender)
    kind = forced_classification or classify(subject, body)
    if kind not in VALID_CLASSIFICATIONS:
        raise ValueError('Invalid classification')
    # For bounces the suppressed/affected address is the recipient, not the sender.
    address = normalize_email(target_email) if (kind == 'BOUNCE' and target_email) else sender

    contact = db.scalar(select(Contact).where(Contact.email == address))
    reply = Reply(external_id=external_id, contact_id=contact.id if contact else None, classification=kind, snippet=(body or subject)[:500])
    db.add(reply)

    if kind == 'UNSUBSCRIBE':
        suppress(db, address, 'unsubscribe')
        domain = address.partition('@')[2]
        # Whole-domain suppression on an explicit unsubscribe prevents other
        # addresses of the company from being approached after an opt-out.
        if domain:
            suppress_domain(db, domain, 'company_unsubscribe')
    elif kind == 'BOUNCE':
        suppress(db, address, 'bounce')

    if contact:
        leads = db.scalars(select(CampaignLead).where(CampaignLead.contact_id == contact.id)).all()
        stop_state = _STOP_STATES.get(kind)
        if stop_state:
            for lead in leads:
                lead.state = stop_state
                for draft in db.scalars(select(Email).where(Email.campaign_lead_id == lead.id, Email.status == 'draft')):
                    draft.status = 'cancelled'

    action = {'UNSUBSCRIBE': 'OPT_OUT', 'BOUNCE': 'EMAIL_BOUNCED'}.get(kind, 'REPLY_RECEIVED')
    db.add(AuditLog(action=action, entity_type='contact', entity_id=contact.id if contact else None, detail=kind[:50]))
    db.commit()
    return kind
