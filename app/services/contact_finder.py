"""Public business contact discovery on the official company website.

Only general, publicly published business addresses are collected
(``info@``, ``contact@``, ``office@``, ``sales@``, ``administratie@`` … on the
company's own domain). No hidden or protected personal data is gathered and no
personal email address is guessed.
"""
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from app.config import settings
from app.services.deduplication import normalize_email

# Obfuscation patterns, e.g. "info[at]domain[dot]nl" / "info (at) domain (dot) nl".
_OBFUSCATED_RE = re.compile(r'([\w.+-]+)\s*[\[({]?\s*at\s*[\])}]?\s*([\w-]+)\s*[\[({]?\s*do?t\s*[\])}]?\s*([a-zA-Z]{2,})', re.IGNORECASE)
_MASKED_RE = re.compile(r'([\w.+-]+)@([\w-]+)\s*[,;·]([a-zA-Z]{2,})')
_LINK_RE = re.compile(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}')


def _general_localparts() -> set[str]:
    return {part.strip().casefold() for part in settings().general_contact_localparts.split(',') if part.strip()}


def _candidate_email(raw: str) -> str:
    """Return normalized email if in the shape of a tolerable general address."""
    candidate = normalize_email(raw.strip(' \t.,;:<>()[]{}“”"').strip())
    local, at, host = candidate.partition('@')
    if not at or not host:
        return ''
    if not re.fullmatch(r'[\w.+-]+', local) or not re.fullmatch(r'[\w.-]+', host) or '.' not in host:
        return ''
    return candidate


def _extract_from_text(text: str) -> list[str]:
    emails: list[str] = []
    # Obfuscated "at"/"dot" markers (handled before any cleaning).
    for match in _OBFUSCATED_RE.finditer(text):
        emails.append(f'{match.group(1)}@{match.group(2)}.{match.group(3)}')
    # Tilted addresses, e.g. "info@domain,nl" (domain part has no dot yet) —
    # the domain group deliberately excludes "." so real addresses are not
    # corrupted when followed by a comma separator.
    mapped = _MASKED_RE.sub(r'\1@\2.\3', text)
    for match in _LINK_RE.finditer(mapped):
        emails.append(match.group(0))
    return emails


def find_contacts(pages: dict[str, str], website: str) -> list[dict]:
    """Return the general business contacts found on the official site's pages."""
    domain = (urlsplit(website).hostname or '').lower()
    bare_domain = domain.removeprefix('www.')
    general = _general_localparts()
    found: dict[str, dict] = {}
    for url, html in pages.items():
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(' ', strip=True)
        mailto_hrefs = ' '.join(unquote(a.get('href', '')) for a in soup.select('a[href^="mailto:"]'))
        candidate_texts = _extract_from_text(text + ' ' + mailto_hrefs)
        for raw in candidate_texts:
            email = _candidate_email(raw)
            if not email:
                continue
            local, _, host = email.partition('@')
            # General box on the company's own domain only — this rules out
            # third-party, free-mail or guessed personal addresses.
            if host != bare_domain:
                continue
            if local not in general and not local.startswith('info'):
                continue
            found[email] = {
                'email': email,
                'email_source_url': url,
                'contact_type': 'general',
                'confidence_score': 95 if local in general else 85,
                'verification_status': 'publicly_listed',
            }
    return list(found.values())
