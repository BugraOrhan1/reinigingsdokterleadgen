"""Unit tests for lead discovery helpers (hermetic — DNS is stubbed)."""
import socket

from app.services import lead_finder, website_analyzer


def _resolve(monkeypatch, ip='93.184.216.34'):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, type, 6, '', (ip, port))])


def test_normalize_website_adds_scheme(monkeypatch):
    _resolve(monkeypatch)
    # OSM publishes websites with or without a scheme; we make them well-formed.
    assert lead_finder._normalize_website('www.voorbeeld.nl') == 'https://www.voorbeeld.nl'
    assert lead_finder._normalize_website('https://voorbeeld.nl') == 'https://voorbeeld.nl'
    assert lead_finder._normalize_website('HTTPS://VOORBEELD.NL') == 'https://voorbeeld.nl'


def test_normalize_website_rejects_garbage(monkeypatch):
    _resolve(monkeypatch)
    assert lead_finder._normalize_website('') == ''
    assert lead_finder._normalize_website('not a url') == ''
    assert lead_finder._normalize_website('ftp://voorbeeld.nl') == ''


def test_public_url_guard(monkeypatch):
    _resolve(monkeypatch)
    assert website_analyzer.public_url('https://voorbeeld.nl')
    assert not website_analyzer.public_url('https://user:pass@voorbeeld.nl')
    assert not website_analyzer.public_url('javascript:alert(1)')


def test_public_url_rejects_loopback(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, type, 6, '', ('127.0.0.1', port))])
    assert not website_analyzer.public_url('https://voorbeeld.nl')


def test_public_url_rejects_private_ipv4(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, type, 6, '', ('10.0.0.1', port))])
    assert not website_analyzer.public_url('https://voorbeeld.nl')
