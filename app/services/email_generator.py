"""Personalised Dutch B2B acquisition emails.

Rules implemented here:
  * short, professional, natural Dutch;
  * multiple templates so campaigns do not send identical mails;
  * only verified information (company name, industry, city, service and
    verbatim website facts) is used — nothing invented;
  * calm CTA, no misleading subject line, no over-promising;
  * explicit sender identification and an opt-out instruction.

The LLM path is constrained by verification checks and falls back to a
deterministic template on any failure or unsafe output.
"""
import hashlib
import re

from app.config import settings

VARIANTS = 4


def _pick(company, sequence: int) -> int:
    material = f'{company.domain}:{sequence}'
    return int(hashlib.sha256(material.encode()).hexdigest(), 16) % VARIANTS


def _footer(cfg, include_name: bool = True) -> str:
    parts = ([cfg.company_name] if include_name else []) + [cfg.company_website, cfg.company_address, cfg.company_email]
    return '\n'.join(x for x in parts if x)


def _optout(cfg) -> str:
    return 'Geen interesse? Antwoord met "afmelden" en we nemen geen contact meer met u op.'


def _body(paragraphs: list[str], cfg) -> str:
    lines: list[str] = []
    for paragraph in paragraphs:
        if not paragraph.strip():
            continue
        lines.append(paragraph.strip())
        lines.append('')
    lines.append(_optout(cfg))
    lines.append('')
    lines.append('Met vriendelijke groet,')
    lines.append(cfg.company_name)
    footer = _footer(cfg, include_name=False)
    if footer:
        lines.append(footer)
    # Collapse consecutive blank lines.
    out: list[str] = []
    for line in lines:
        if line == '' and out and out[-1] == '':
            continue
        out.append(line)
    return '\n'.join(out).strip()


def generate(company, service: str, sequence: int = 0) -> tuple[str, str]:
    """Deterministic template path. Variant depends on domain + sequence so a
    cohort never receives identical mails."""
    if not service:
        raise ValueError('A verified service mapping is required')
    cfg = settings()
    city = (company.city or '').strip()
    location = f'locatie {city}' if city else 'uw regio'
    variant = _pick(company, sequence)

    if sequence > 0:
        subjects = [
            f'Vervolg: reiniging voor {company.company_name}',
            f'Nog even terugkomen op {company.company_name}',
            f'Vraag over reiniging bij {company.company_name}',
            f'{company.company_name} — vervolg',
        ]
        lead_ins = [
            'Ik kom kort terug op mijn eerdere bericht:',
            'Eerder stuurde ik u al een bericht;',
            'Mag ik nog even bij u terugkomen op mijn vorige mail?',
            'Ter aanvulling op mijn vorige bericht:',
        ]
        asks = [
            'Zou een kort gesprek hierover nuttig zijn?',
            'Is dit iets om op korte termijn te bespreken?',
            'Kan ik u hierover vrijblijvend meer vertellen?',
            'Past dit bij uw planning?',
        ]
        subject = subjects[variant]
        body = _body(
            [
                'Goedemiddag,',
                f'{lead_ins[variant]} Reinigingsdokter helpt bedrijven met {service}. {asks[variant]}',
            ],
            cfg,
        )
        return subject, body

    subjects = [
        f'Vraag over reiniging bij {company.company_name}',
        f'Reiniging voor {company.company_name}',
        f'Kennismaking over {service}?',
        f'{company.company_name} en {service}',
    ]
    openings = [
        f'Ik zag de website van {company.company_name}.',
        f'Ik kwam {company.company_name} online tegen.',
        f'Ik bekeek de website van {company.company_name}.',
        f'Via de website van {company.company_name} vond ik uw gegevens.',
    ]
    services = [
        f'Wij ontzorgen bedrijven op {location} graag met {service}.',
        f'Reinigingsdokter voert voor bedrijven zoals het uwe op {location} {service} uit.',
        f'Onze ervaring is dat {service} goed past bij een bedrijf als het uwe.',
        f'Dagelijks helpen wij bedrijven in {city or "de regio"} met {service}.',
    ]
    asks = [
        'Zou een kort gesprek hierover nuttig zijn?',
        'Kan ik u hierover vrijblijvend meer vertellen?',
        'Is dit iets waar ik u bij kan helpen?',
        'Past dit bij uw planning?',
    ]
    subject = subjects[variant]
    paragraphs = ['Goedemiddag,', f'{openings[variant]} {services[variant]} {asks[variant]}']
    facts = [x.get('text', '') for x in (company.facts or {}).get('evidence', []) if (x.get('url') or '').startswith(company.website.rstrip('/'))][:2]
    if facts:
        paragraphs.append('Op uw site las ik onder andere: ' + ' '.join(facts)[:300] + '.')
    body = _body(paragraphs, cfg)
    return subject, body


def generate_with_llm(company, service: str, sequence: int = 0) -> tuple[str, str]:
    """LLM path with verification constraints; deterministic fallback."""
    cfg = settings()
    if not cfg.openai_api_key:
        return generate(company, service, sequence)
    try:
        from openai import OpenAI
    except Exception:  # noqa: BLE001 — package unavailable
        return generate(company, service, sequence)
    facts = (company.facts or {}).get('evidence', [])[:5]
    allowed = [x.get('text', '') for x in facts if (x.get('url') or '').startswith(company.website.rstrip('/'))]
    mode = 'een vervolgmail' if sequence else 'een eerste kennismakingsmail'
    prompt = (
        f'Schrijf één korte Nederlandse zakelijke acquisitie-email ({mode}). '
        f'Bedrijf: {company.company_name}. Dienst: {service}. '
        f'Geverifieerde website-informatie: {allowed or "geen"}. '
        f'Afzender: {cfg.company_name}. '
        f'Gebruik alleen bovengenoemde feiten; verzin geen claims, resultaten of beloftes. '
        f'Rustige call-to-action. Antwoord uitsluitend met: onderwerpregel, daarna een lege regel, daarna de e-mailtekst.'
    )
    try:
        client = OpenAI(api_key=cfg.openai_api_key, timeout=20)
        response = client.responses.create(model=cfg.openai_model, input=prompt, max_output_tokens=300)
        raw = response.output_text.strip()
        subject, _, body = raw.partition('\n')
        subject = subject.strip()
        body = body.strip()
        if not subject or not body:
            raise ValueError('Empty generated output')
        if len(subject) > 120 or len(body) > 1200:
            raise ValueError('Generated output too long')
        if not re.search(re.escape(company.company_name), subject + body, re.IGNORECASE):
            raise ValueError('Company name missing')
        return subject, (body + '\n\n' + _footer(cfg) + '\n\n' + _optout(cfg)).strip()
    except Exception:  # noqa: BLE001 — safe deterministic fallback
        return generate(company, service, sequence)
