import email
import imaplib
import re
from email.header import decode_header
from email.utils import parseaddr
from sqlalchemy.orm import Session
from app.config import settings
from app.services.inbox_processor import classify, process_reply


def _text(message):
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == 'text/plain' and part.get_content_disposition() != 'attachment':
                return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')[:5000]
        return ''
    return (message.get_payload(decode=True) or b'').decode(message.get_content_charset() or 'utf-8', errors='replace')[:5000]


def poll_inbox(db: Session) -> int:
    cfg = settings()
    if not (cfg.imap_host and cfg.imap_username and cfg.imap_password):
        return 0
    count = 0
    with imaplib.IMAP4_SSL(cfg.imap_host) as client:
        client.login(cfg.imap_username, cfg.imap_password)
        client.select('INBOX', readonly=True)
        status, ids = client.search(None, 'ALL')
        if status != 'OK':
            return 0
        for uid in ids[0].split()[-100:]:
            status, parts = client.fetch(uid, '(RFC822)')
            if status != 'OK' or not parts or not isinstance(parts[0], tuple):
                continue
            message = email.message_from_bytes(parts[0][1])
            sender = parseaddr(message.get('From', ''))[1]
            subject = str(email.header.make_header(decode_header(message.get('Subject', ''))))
            body = _text(message)
            kind = classify(subject, body)
            target = ''
            if kind == 'BOUNCE':
                target = message.get('X-Failed-Recipients', '').split(',')[0].strip()
                if not target:
                    match = re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', body)
                    target = match.group(0) if match else ''
                if not target:
                    continue
            external_id = message.get('Message-ID') or f'imap:{uid.decode()}'
            if process_reply(db, external_id, sender, subject, body, target_email=target) != 'duplicate':
                count += 1
    return count
