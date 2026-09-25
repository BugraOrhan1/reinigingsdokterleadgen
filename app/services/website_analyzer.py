import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup

USER_AGENT = 'ReinigingsdokterLeadResearch/1.0 (public pages only)'
PAGE_HINTS = ('contact', 'over', 'diensten', 'services', 'locaties')


def public_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (socket.gaierror, ValueError):
        return False


def fetch_pages(website: str) -> dict[str, str]:
    if not public_url(website):
        raise ValueError('Website must resolve to a public address')
    origin = urlsplit(website)
    root = f'{origin.scheme}://{origin.netloc}'
    pages = {}
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False, headers={'User-Agent': USER_AGENT}) as client:
        robots_response = client.get(root + '/robots.txt')
        if robots_response.status_code not in (200, 404, 410):
            return pages
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines() if robots_response.status_code == 200 else [])
        queue = [website]
        while queue and len(pages) < 5:
            url = queue.pop(0)
            if url in pages or not public_url(url) or urlsplit(url).hostname != origin.hostname or not robots.can_fetch(USER_AGENT, url):
                continue
            response = client.get(url)
            if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', '') or len(response.content) > 500_000:
                continue
            pages[url] = response.text
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.select('a[href]'):
                target = urljoin(url, a['href']).split('#')[0]
                if urlsplit(target).hostname == origin.hostname and any(h in target.lower() for h in PAGE_HINTS) and target not in queue:
                    queue.append(target)
    return pages


def extract_facts(pages: dict[str, str]) -> dict:
    evidence = []
    for url, html in pages.items():
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        for selector in ('h1', 'h2', 'meta[name="description"]'):
            for tag in soup.select(selector)[:3]:
                phrase = tag.get('content', '') if tag.name == 'meta' else tag.get_text(' ', strip=True)
                if phrase and len(phrase) < 250:
                    evidence.append({'text': phrase, 'url': url})
    return {'evidence': evidence[:15]}
