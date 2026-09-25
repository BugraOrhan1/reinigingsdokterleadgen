import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from app.services.deduplication import normalize_email

GENERAL = ('info', 'contact', 'office', 'sales', 'administratie')
EMAIL_RE = re.compile(r'(?<![\w.])([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})(?![\w.])')


def find_contacts(pages: dict[str, str], website: str) -> list[dict]:
    domain = (urlsplit(website).hostname or '').lower().removeprefix('www.')
    found = {}
    for url, html in pages.items():
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ', strip=True) + ' ' + ' '.join(a.get('href', '') for a in soup.select('a[href^="mailto:"]'))
        for match in EMAIL_RE.finditer(text):
            email = normalize_email(match.group(1).strip('.,;:'))
            local, _, host = email.partition('@')
            if host != domain or local not in GENERAL:
                continue
            found[email] = {'email': email, 'email_source_url': url, 'contact_type': 'general', 'confidence_score': 90, 'verification_status': 'publicly_listed'}
    return list(found.values())
