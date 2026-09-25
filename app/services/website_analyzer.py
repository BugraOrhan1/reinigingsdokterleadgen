"""Public website analysis: reading only ordinary, public business pages.

Rules enforced here:
  * only http(s) URLs that resolve to global (non-private) addresses (SSRF guard);
  * a polite, identifying User-Agent;
  * robots.txt is honoured page by page;
  * a small, bounded crawl (home + a few relevant subpages) with throttling;
  * no form submission, login, CAPTCHA solving or authenticated content.

Only information actually present on the site is collected — nothing is
inferred or invented.
"""
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import settings

PAGE_HINTS = ('contact', 'over', 'diensten', 'services', 'locaties', 'over-ons', 'overons')


def public_url(url: str) -> bool:
    """True when ``url`` is http(s), has no credentials and resolves exclusively
    to global (public) addresses — the SSRF guard for the crawler."""
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not addresses:
        return False
    for item in addresses:
        try:
            ip = ipaddress.ip_address(item[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return False
    return True


def _throttle() -> None:
    """Small polite delay between requests to the same process."""
    elapsed = time.monotonic() - getattr(_throttle, '_last', 0.0)
    if elapsed < 0.3:
        time.sleep(0.3 - elapsed)
    _throttle._last = time.monotonic()


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    """One GET with a single retry on transient transport errors."""
    for attempt in (1, 2):
        _throttle()
        try:
            return client.get(url)
        except httpx.TransportError:
            if attempt == 2:
                return None
            time.sleep(1.0)
    return None


def fetch_pages(website: str, max_pages: int | None = None) -> dict[str, str]:
    """Return ``{url: html}`` for the homepage and relevant public subpages."""
    if not public_url(website):
        raise ValueError('Website must resolve to a public address')
    cfg = settings()
    limit = max_pages or cfg.max_pages_per_site
    origin = urlsplit(website)
    root = f'{origin.scheme}://{origin.netloc}'
    pages: dict[str, str] = {}
    with httpx.Client(
        timeout=cfg.request_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers={'User-Agent': cfg.request_user_agent},
    ) as client:
        robots = RobotFileParser()
        robots_url = root + '/robots.txt'
        robots_response = _get(client, robots_url)
        if robots_response is None:
            return pages
        if robots_response.status_code in (200, 404, 410):
            robots.parse(robots_response.text.splitlines() if robots_response.status_code == 200 else [])
        else:
            # If robots.txt itself refuses access, behave conservatively and stop.
            return pages
        queue = [website]
        seen: set[str] = set()
        while queue and len(pages) < limit:
            url = queue.pop(0)
            parsed = urlsplit(url)
            if url in seen or parsed.hostname != origin.hostname:
                continue
            seen.add(url)
            if not public_url(url) or not robots.can_fetch(cfg.request_user_agent, url):
                continue
            response = _get(client, url)
            if response is None or response.status_code != 200:
                continue
            content_type = response.headers.get('content-type', '')
            if 'text/html' not in content_type or len(response.content) > 500_000:
                continue
            pages[url] = response.text
            soup = BeautifulSoup(response.text, 'html.parser')
            for anchor in soup.select('a[href]'):
                target = urljoin(url, anchor.get('href', '')).split('#')[0].split('?')[0]
                if len(pages) + len(queue) >= limit * 4:
                    break
                if (
                    urlsplit(target).hostname == origin.hostname
                    and any(h in target.lower() for h in PAGE_HINTS)
                    and target not in seen
                    and target not in queue
                ):
                    queue.append(target)
    return pages


def extract_facts(pages: dict[str, str]) -> dict:
    """Extract small, verbatim text evidence (titles/headings/descriptions).

    Only exact phrases from the page are stored, together with the page URL,
    so downstream generation can only use real, verifiable website content.
    """
    evidence: list[dict] = []
    sources: set[str] = set()
    for url, html in pages.items():
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        selectors = ('h1', 'h2', 'meta[name="description"]')
        for selector in selectors:
            for tag in soup.select(selector)[:3]:
                if tag.name == 'meta':
                    phrase = tag.get('content', '') or ''
                else:
                    phrase = tag.get_text(' ', strip=True)
                key = (url, phrase[:200])
                if key in sources:
                    continue
                sources.add(key)
                if phrase and len(phrase) < 250:
                    evidence.append({'text': phrase, 'url': url})
        if len(evidence) >= 15:
            break
    return {'evidence': evidence[:15]}
