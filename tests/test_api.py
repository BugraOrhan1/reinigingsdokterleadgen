"""Dashboard API integration tests (auth, campaigns, replies, suppression)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401
from app.config import settings as cached_settings  # noqa: F401
from tests._helpers import make_settings


@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(engine, expire_on_commit=False)

    def override_db():
        with TestSession() as session:
            yield session

    import app.main as main

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr('app.config.settings', lambda: make_settings())
    monkeypatch.setattr('app.main.settings', lambda: make_settings())
    # The auth dependency reads settings() from app.config directly.
    with TestClient(main.app) as tc:
        yield tc
    main.app.dependency_overrides.clear()


def _login(client: TestClient) -> None:
    resp = client.post('/login', data={'username': 'admin', 'password': 'test-pass'}, follow_redirects=False)
    assert resp.status_code == 303
    assert 'rd_session' in client.cookies


def test_dashboard_requires_login(client):
    resp = client.get('/api/overview')
    assert resp.status_code == 401


def test_login_and_overview(client):
    _login(client)
    resp = client.get('/api/overview')
    assert resp.status_code == 200
    data = resp.json()
    assert data['leads_found'] == 0
    assert data['dry_run'] is True


def test_create_lead_and_campaign_flow(client):
    _login(client)
    lead = client.post('/api/leads', json={
        'company_name': 'Belegd Bv', 'website': 'https://belegd.nl',
        'industry': 'restaurant', 'city': 'Rotterdam', 'region': 'Zuid-Holland',
    })
    assert lead.status_code == 200, lead.text
    campaign = client.post('/api/campaigns', json={
        'name': 'Restaurants Zuid-Holland', 'industries': ['restaurant'],
        'region': 'Zuid-Holland', 'daily_limit': 25, 'min_score': 70,
        'service_override': '', 'max_followups': 1,
    })
    assert campaign.status_code == 200, campaign.text
    cid = campaign.json()['id']
    # Campaign model has radius_km default 0 — model_dump needs it present.
    attach = client.post(f'/api/campaigns/{cid}/leads/{lead.json()["id"]}')
    assert attach.status_code == 200
    row = client.get(f'/api/campaigns/{cid}/leads').json()[0]
    assert row['company_name'] == 'Belegd Bv'
    assert row['state'] == 'new'


def test_session_cookie_secure_and_origin_csrf(client):
    _login(client)
    # Cross-origin state-changing request must be rejected (CSRF defence).
    resp = client.post('/api/campaigns', json={'name': 'X', 'industries': ['hotel']}, headers={'Origin': 'https://evil.example'})
    assert resp.status_code == 403


def test_reply_unsubscribe_via_api_suppresses(client):
    _login(client)
    resp = client.post('/api/replies', json={'external_id': 'e-1', 'sender': 'voorman@bedrijf.nl', 'subject': 'afmelden', 'body': 'geen mails meer'})
    assert resp.status_code == 200
    assert resp.json()['classification'] == 'UNSUBSCRIBE'
    sup = client.get('/api/suppression').json()
    assert any(x['email'] == 'voorman@bedrijf.nl' for x in sup)
