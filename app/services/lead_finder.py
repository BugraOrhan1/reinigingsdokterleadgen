import re
import httpx
from app.services.website_analyzer import public_url
from app.config import settings

TAGS = {
    'restaurant': ('amenity', 'restaurant'), 'restaurants': ('amenity', 'restaurant'),
    'hotel': ('tourism', 'hotel'), 'sportschool': ('leisure', 'fitness_centre'),
    'kantoor': ('office', None), 'winkel': ('shop', None), 'garage': ('shop', 'car_repair'),
    'autobedrijf': ('shop', 'car'), 'school': ('amenity', 'school'),
    'kinderdagverblijf': ('amenity', 'kindergarten'), 'zorgpraktijk': ('healthcare', None),
    'magazijn': ('building', 'warehouse'), 'vastgoedbedrijf': ('office', 'estate_agent'),
}


def discover(industry: str, region: str, city: str = '') -> list[dict]:
    """Small, attributed OSM query. Configure a dedicated Overpass instance for volume."""
    key, value = TAGS.get(industry.casefold(), (None, None))
    if not key or not (region or city):
        return []
    place = city or region
    if not re.fullmatch(r'[\w\s-]{2,100}', place, re.UNICODE):
        raise ValueError('Invalid area')
    tag_filter = f'["{key}"="{value}"]' if value else f'["{key}"]'
    query = f'[out:json][timeout:20];area["name"="{place}"]->.a;(nwr(area.a){tag_filter}["website"];);out tags 50;'
    with httpx.Client(timeout=30) as client:
        response = client.post(settings().discovery_api_url or 'https://overpass-api.de/api/interpreter', data={'data': query}, headers={'User-Agent': 'ReinigingsdokterLeadResearch/1.0'})
        response.raise_for_status()
    results = []
    for item in response.json().get('elements', []):
        tags = item.get('tags', {})
        website = tags.get('website', '')
        if not tags.get('name') or not public_url(website):
            continue
        results.append({'company_name': tags['name'], 'website': website, 'industry': industry, 'city': tags.get('addr:city', city), 'region': region, 'address': ' '.join(filter(None, [tags.get('addr:street'), tags.get('addr:housenumber')])), 'source': 'OpenStreetMap', 'source_url': f"https://www.openstreetmap.org/{item['type']}/{item['id']}"})
    return results
