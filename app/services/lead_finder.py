"""Business discovery via publicly licensed, attributed sources.

Primary source: OpenStreetMap through the Overpass API (ODbL). Geographic
areas are resolved through the public Nominatim service (ODbL) under the
GDPV/usage policy: a descriptive User-Agent, low request rate and caching.

Both sources are used only for publicly available data, without any login,
CAPTCHA bypass or scraping behind authentication. Attribution ("OpenStreetMap")
is recorded per lead in ``source_url``.
"""
import json
import re
import time
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.services.website_analyzer import public_url

# Canonical, lower-cased industry names -> Overpass tag(s).
# A keyword pushes discovery to Nominatim name search so the exact Overpass
# tag does not have to be known in advance (still attributed to OSM).
TAGS = {
    'restaurant': ('amenity', 'restaurant'),
    'restaurants': ('amenity', 'restaurant'),
    'hotel': ('tourism', 'hotel'),
    'hotels': ('tourism', 'hotel'),
    'sportschool': ('leisure', 'fitness_centre'),
    'sportscholen': ('leisure', 'fitness_centre'),
    'kantoor': ('office', None),
    'kantoren': ('office', None),
    'winkel': ('shop', None),
    'winkels': ('shop', None),
    'garage': ('shop', 'car_repair'),
    'garages': ('shop', 'car_repair'),
    'autobedrijf': ('shop', 'car'),
    'autobedrijven': ('shop', 'car'),
    'vastgoedbedrijf': ('office', 'estate_agent'),
    'vastgoedbedrijven': ('office', 'estate_agent'),
    'vve-beheerder': ('office', 'property_management'),
    'vve-beheerders': ('office', 'property_management'),
    'school': ('amenity', 'school'),
    'scholen': ('amenity', 'school'),
    'kinderdagverblijf': ('amenity', 'kindergarten'),
    'kinderdagverblijven': ('amenity', 'kindergarten'),
    'zorgpraktijk': ('healthcare', None),
    'zorgpraktijken': ('healthcare', None),
    'magazijn': ('building', 'warehouse'),
    'magazijnen': ('building', 'warehouse'),
    'bedrijfspand': ('building', 'commercial'),
    'bedrijfspanden': ('building', 'commercial'),
}

DEFAULT_OVERPAST = 'https://overpass-api.de/api/interpreter'
NOMINATIM_SEARCH = 'https://nominatim.openstreetmap.org/search'


def _normalize_website(value: str) -> str:
    """OSM publishes websites with or without a scheme; make them well-formed."""
    website = (value or '').strip().lower()
    if not website:
        return ''
    if '://' not in website:
        website = 'https://' + website.lstrip('/')
    host = urlsplit(website).hostname or ''
    # Reject malformed hostnames early (spaces, strange characters) before DNS.
    if not host or not re.fullmatch(r'[a-z0-9.-]+', host) or '..' in host:
        return ''
    return website if public_url(website) else ''


class DiscoveryError(RuntimeError):
    """Raised when a public data source refuses the request or misbehaves."""


def _client() -> httpx.Client:
    cfg = settings()
    return httpx.Client(timeout=cfg.request_timeout_seconds + 20, headers={'User-Agent': cfg.request_user_agent})


def _overpass_url() -> str:
    return settings().discovery_api_url or DEFAULT_OVERPAST


def _area_for(city: str, region: str) -> dict:
    if not city and not region:
        raise ValueError('city or region required')
    city_name = (city or '').strip()
    region_name = (region or '').strip()
    place = city_name or region_name
    if not re.fullmatch(r'[\w\s.\'-]{2,100}', place, re.UNICODE):
        raise ValueError('Invalid area name')

    cache_key = (_area_for, city_name.casefold(), region_name.casefold())
    cached = getattr(_area_for, '_cache', None)
    if cached is None:
        cached = {}
        _area_for._cache = cached
    stamp = getattr(_area_for, '_last_call', 0.0)
    elapsed = time.monotonic() - stamp
    if elapsed < 1.2:
        time.sleep(1.2 - elapsed)
    _area_for._last_call = time.monotonic()

    cfg = settings()
    if cache_key in cached:
        return cached[cache_key]

    # Prefer a region-qualified query so "Rotterdam" is unambiguous.
    display_name = city_name + (', ' + region_name if region_name else ', Nederland')
    base = dict(
        q=display_name,
        format='jsonv2',
        addressdetails=0,
        limit=1,
    )
    parsed = urlsplit(cfg.nominatim_user_agent)
    params = [('useragent', cfg.nominatim_user_agent or parsed.path or 'leadgen'), ('email', parsed.hostname or '')]
    if cfg.discovery_api_key:
        params.append(('key', cfg.discovery_api_key))

    with _client() as client:
        resp = client.get(NOMINATIM_SEARCH, params=base + params)
        resp.raise_for_status()
        payload = json.loads(resp.text)
        resp.read()  # drain
        if isinstance(payload, dict):
            raise DiscoveryError(f'data source refused: {payload}')
        if not payload:
            raise DiscoveryError(f'unknown area: {place}')
        first = payload[0]
        boundingbox = first.get('boundingbox') or [None, None, None, None]
        try:
            near = float(first.get('lat') or 0), float(first.get('lon') or 0)
        except (TypeError, ValueError):
            near = (0.0, 0.0)
        area = {'south': boundingbox[0], 'north': boundingbox[1], 'west': boundingbox[2], 'east': boundingbox[3], 'near': near}
        cached[cache_key] = area
        return area


def discover(industry: str, region: str = '', city: str = '', radius_km: int = 0, company_name: str = '') -> list[dict]:
    """Return public business records for ``industry`` in ``region``/``city``.

    ``radius_km`` narrows results around the resolved area centre. A
    ``company_name`` filters by name (case-insensitive substring). An empty
    result is returned gracefully for unknown industries/areas.
    """
    if not settings().discovery_api_terms_accepted:
        raise DiscoveryError('DISCOVERY_API_TERMS_ACCEPTED=false; acknowledge the data source terms first')

    tag_key, tag_value = TAGS.get(industry.casefold(), (None, None))
    if not tag_key or not (region or city):
        return []

    try:
        area = _area_for(city, region)
    except (DiscoveryError, httpx.HTTPError, ValueError):
        return []

    near = area.get('near') or (0.0, 0.0)

    # Keyword/name search through Nominatim yields OSM-backed results for a
    # natural-language query ("garages in Rotterdam") and is rate limited.
    if company_name:
        extra = _discover_via_nominatim(industry, city, region, company_name, near)
        if extra:
            return _dedupe_by_domain(extra)[:100]

    radius_filter = ''
    if radius_km > 0 and near != (0.0, 0.0):
        radius_filter = f'(around:{radius_km * 1000},{near[0]},{near[1]})'

    area_filter = ''
    bbox = [area.get(k) for k in ('south', 'west', 'north', 'east')]
    if all(v is not None for v in bbox):
        area_filter = f'({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]})'

    tag_filter = f'["{tag_key}"' + (f'="{tag_value}"]' if tag_value else '"]')
    scope = radius_filter or area_filter or ''
    if scope:
        query = f'[out:json][timeout:25];(nwr{scope}{tag_filter}["website"];);out tags 60;'
    else:
        query = f'[out:json][timeout:25];area["name"="{city or region}"]->.a;(nwr(area.a){tag_filter}["website"];);out tags 60;'

    with _client() as client:
        resp = client.post(_overpass_url(), data={'data': query})
        resp.raise_for_status()
        if resp.headers.get('content-type', '').startswith('text/html'):
            raise DiscoveryError('discovery API returned HTML (rate limited or misconfigured)')
        payload = json.loads(resp.text)
        resp.read()

    results: list[dict] = []
    for item in payload.get('elements', []):
        tags = item.get('tags') or {}
        name = (tags.get('name') or '').strip()
        website = _normalize_website(tags.get('website') or '')
        if not name or not website:
            continue
        if company_name and company_name.casefold() not in name.casefold():
            continue
        placement = tags.get('addr:city') or city
        region_name = tags.get('addr:region') or region
        results.append({
            'company_name': name,
            'website': website,
            'industry': industry,
            'city': placement,
            'region': region_name,
            'address': ' '.join(filter(None, [tags.get('addr:street'), tags.get('addr:housenumber')])),
            'source': 'OpenStreetMap',
            'source_url': f"https://www.openstreetmap.org/{item.get('type', 'node')}/{item.get('id')}",
        })
    # De-duplicate by domain (indexable) and cap the total.
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in results:
        host = (urlsplit(row['website']).hostname or '').lower().removeprefix('www.')
        if host in seen:
            continue
        seen.add(host)
        deduped.append(row)
    return deduped[:100]


def _discover_via_nominatim(industry: str, city: str, region: str, company_name: str, near: tuple[float, float]) -> list[dict] | None:
    """Fallback name/keyword search for ``company_name`` (OSM, attributed)."""
    if near == (0.0, 0.0):
        return None
    lat, lon = near
    params = [
        ('q', company_name),
        ('format', 'jsonv2'),
        ('addressdetails', '1'),
        ('limit', '20'),
        ('viewbox', f'{lon - 0.25},{lat - 0.25},{lon + 0.25},{lat + 0.25}'),
        ('bounded', '1'),
    ]
    with _client() as client:
        resp = client.get(NOMINATIM_SEARCH, params=params)
        resp.raise_for_status()
        payload = json.loads(resp.text)
        resp.read()
    if isinstance(payload, dict):
        return None
    rows: list[dict] = []
    for item in payload:
        address = item.get('address') or {}
        name = (item.get('name') or '').strip()
        website = _normalize_website((item.get('extratags') or {}).get('website') or '')
        if not name or not website or company_name.casefold() not in name.casefold():
            continue
        rows.append({
            'company_name': name,
            'website': website,
            'industry': industry,
            'city': address.get('city') or address.get('town') or city,
            'region': address.get('state') or region,
            'address': ' '.join(filter(None, [address.get('road'), address.get('house_number')])),
            'source': 'OpenStreetMap',
            'source_url': f"https://www.openstreetmap.org/{item.get('osm_type', 'node')}/{item.get('osm_id', '')}",
        })
    return rows if rows else None


def _dedupe_by_domain(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        host = (urlsplit(row['website']).hostname or '').lower().removeprefix('www.')
        if host in seen:
            continue
        seen.add(host)
        out.append(row)
    return out
