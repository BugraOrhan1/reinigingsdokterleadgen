from app.config import settings


DEFAULT_SERVICES = {
    'restaurant': 'vloer- en periodieke reiniging', 'restaurants': 'vloer- en periodieke reiniging',
    'hotel': 'meubel- en vloerreiniging', 'sportschool': 'vloer- en periodieke reiniging',
    'kantoor': 'kantoor- en vloerreiniging', 'garage': 'vloer- en intensieve reiniging',
    'autobedrijf': 'vloer- en intensieve reiniging', 'winkel': 'vloerreiniging',
    'school': 'vloer- en periodieke reiniging', 'kinderdagverblijf': 'periodieke reiniging',
    'zorgpraktijk': 'vloer- en periodieke reiniging', 'magazijn': 'vloerreiniging',
    'vastgoedbedrijf': 'periodieke pandreiniging', 'vve-beheerder': 'periodieke pandreiniging',
}


def score_lead(company, contact, campaign, service: str) -> int:
    score = 0
    industries = [x.casefold() for x in (campaign.industries or [])]
    if company.industry.casefold() in industries:
        score += 25
    if campaign.region and company.region.casefold() == campaign.region.casefold():
        score += 20
    elif campaign.city and company.city.casefold() == campaign.city.casefold():
        score += 20
    elif not campaign.region and not campaign.city and company.region.casefold() in settings().service_regions.casefold():
        score += 15
    if service:
        score += 20
    if contact and contact.verification_status == 'publicly_listed':
        score += min(20, contact.confidence_score // 5)
    if company.facts and company.facts.get('evidence'):
        score += 15
    return min(100, score)
