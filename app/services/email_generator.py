import hashlib
import re
from app.config import settings


def generate(company, service: str, sequence: int = 0) -> tuple[str, str]:
    if not service:
        raise ValueError('A verified service mapping is required')
    sender = settings()
    if sequence:
        subject = f'Nog een vraag over reiniging bij {company.company_name}'
        body = f'Goedemiddag,\n\nIk kom kort terug op mijn eerdere bericht over {service} voor {company.company_name}. Als dit nu niet relevant is, laat het gerust weten.\n\nMet vriendelijke groet,\n{sender.company_name}'
    else:
        variant = int(hashlib.sha256(company.domain.encode()).hexdigest(), 16) % 3
        subject = [f'Vraag over reiniging bij {company.company_name}', f'Reiniging voor {company.company_name}', f'Kennismaken over {service}?'][variant]
        intros = [f'Ik zag de website van {company.company_name}.', f'Ik kwam {company.company_name} online tegen.', f'Ik bekeek de website van {company.company_name}.']
        body = f'Goedemiddag,\n\n{intros[variant]} {sender.company_name} helpt bedrijven met {service}. Zou een kort gesprek hierover nuttig zijn?\n\nMet vriendelijke groet,\n{sender.company_name}'
    footer = '\n'.join(filter(None, [sender.company_website, sender.company_address, sender.company_email]))
    body += f'\n{footer}\n\nGeen interesse? Antwoord met afmelden; wij nemen dan geen contact meer op.'
    return subject, body


def generate_with_llm(company, service: str, sequence: int = 0) -> tuple[str, str]:
    """LLM output is deliberately constrained; deterministic template is the safe fallback."""
    cfg = settings()
    if not cfg.openai_api_key:
        return generate(company, service, sequence)
    from openai import OpenAI
    facts = (company.facts or {}).get('evidence', [])[:5]
    allowed = [x.get('text', '') for x in facts if x.get('url', '').startswith(company.website.rstrip('/'))]
    prompt = f'Write one short Dutch B2B acquisition email. Company: {company.company_name}. Service: {service}. Verified website facts: {allowed}. Sender: {cfg.company_name}. Do not invent facts, promises or results. Return exactly subject then newline then body. Calm CTA. No markdown.'
    try:
        response = OpenAI(api_key=cfg.openai_api_key, timeout=20).responses.create(model=cfg.openai_model, input=prompt, max_output_tokens=300)
        raw = response.output_text.strip()
        subject, body = raw.split('\n', 1)
        if len(subject) > 120 or len(body) > 1200 or not re.search(re.escape(company.company_name), subject + body, re.I):
            raise ValueError('Unsafe generated output')
        footer = '\n'.join(filter(None, [cfg.company_name, cfg.company_website, cfg.company_address, cfg.company_email]))
        return subject.strip(), body.strip() + f'\n\n{footer}\nGeen interesse? Antwoord met afmelden.'
    except Exception:
        return generate(company, service, sequence)
