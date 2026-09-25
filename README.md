# Reinigingsdokter — AI Lead Generation & Outreach

Een productieklare Python-applicatie die automatisch zakelijke leads vindt, kwalificeert en gepersonaliseerde acquisitiemails opstelt en — uitsluitend binnen ingestelde, veilige limieten — verstuurt. Gebouwd met FastAPI, SQLAlchemy, PostgreSQL en APScheduler.

**Standaard is alles veilig:** `DRY_RUN=true`, `LIVE_SEND_ENABLED=false` en `ENABLE_AUTOMATION=false`. Er wordt dan niets verzonden.

---

## Flow

    leads zoeken → website analyseren → zakelijk contact vinden → kwalificeren
    → relevante dienst bepalen → mail genereren → dedup/suppression check
    → bewaakte verzending → resultaat opslaan → replies/bounces verwerken
    → follow-ups plannen → dashboard/statistieken bijwerken

---

## Structuur

    app/
      main.py            FastAPI-dashboard + JSON API (auth, CSRF, rate limit)
      config.py          alle instellingen via omgevingsvariabelen
      database.py        SQLAlchemy engine/sessies
      models.py          ORM-modellen (bedrijven, contacten, campagnes, mails, …)
      services/
        lead_finder.py   OpenStreetMap/Overpass + Nominatim (attributed, gelimiteerd)
        website_analyzer.py  analyse van normale openbare bedrijfspagina's
        contact_finder.py    algemene zakelijke adressen (info@, contact@, …)
        lead_qualifier.py    0–100 relevantiescore (alleen relevantieselectie)
        email_generator.py   Nederlandse, gepersonaliseerde mails (LLM of template)
        mailer.py            SMTP/Gmail met harde pre-flight-controles
        followup.py          follow-ups binnen MAX_FOLLOWUPS
        inbox_processor.py   classificatie (INTERESTED/BOUNCE/UNSUBSCRIBE/…)
        inbox_polling.py     IMAP-lezer voor replies en bounces
        deduplication.py     domein/naam/e-mail deduplicatie, succesrecords
      workers/
        tasks.py         idempotente, fouttolerante taken
        scheduler.py     APScheduler-rooster
      static/            dashboard-frontend (login.html + index.html)
    alembic/              migraties (0001 initieel, 0002 e-mailkolommen)
    tests/                unit- + integratietests (sqlite-gestuurd, 27 stuks)
    scripts/smoke_test.py lonend-to-end-pipeline test
    .env.example
    requirements.txt
    docker-compose.yml

---

## Snel starten (lokaal)

1. Installeer Python 3.11/3.12 en kopieer `.env.example` naar `.env`.
2. Kies een lange willekeurige `APP_SECRET` en een sterk `ADMIN_PASSWORD` (`openssl rand -hex 32`).
3. Voor een snelle draai zonder PostgreSQL:

       export DATABASE_URL=sqlite:////tmp/rd.db
       export APP_SECRET=... ADMIN_PASSWORD=...
       uvicorn app.main:app --host 0.0.0.0 --port 8000

4. Open `http://127.0.0.1:8000`, log in en maak een campagne.

## Met Docker Compose

    cp .env.example .env        # vul POSTGRES_PASSWORD en DATABASE_URL in
    docker compose up --build -d
    # dashboard: http://127.0.0.1:8000 (migraties draaien automatisch)

De Compose-poort luistert op localhost. Voor een VPS plaats je een HTTPS-reverse-proxy
voor de app en zet je `COOKIE_SECURE=true`.

---

## E-mail meteen versturen (live)

1. Vul `COMPANY_ADDRESS`, `COMPANY_EMAIL` en SMTP of Gmail OAuth in.
2. Verifieer eerst de hele pipeline in dry-run (zie “Testen”).
3. Zet daarna per omgeving: `ENABLE_AUTOMATION=true`, `DRY_RUN=false`, `LIVE_SEND_ENABLED=true`.
4. Begin met lage limieten: `DAILY_EMAIL_LIMIT`, `HOURLY_EMAIL_LIMIT`, `MIN_DELAY_SECONDS`.

Voordat een mail wordt verzonden controleert het systeem in vaste volgorde of:

1. het bedrijf al benaderd werd (en de campagne actief is),
2. het adres op de suppression-/opt-outlijst staat,
3. het adres publiek vermeld en voldoende betrouwbaar is,
4. de leadscore boven de drempel ligt,
5. de dagelijkse/uurlimiet of campagnelimiet is bereikt,
6. de campagne actief is en de live-configuratie compleet is.

Pas daarna wordt — geclaimd vóór netwerk-io — daadwerkelijk verzonden. Een crash ná
de claim kan nooit tot dubbele mails leiden.

---

## Compliance & veiligheid

- Alleen **openbare** bronnen/API's die we mogen gebruiken (OpenStreetMap/Overpass, Nominatim);
  afkomst staat per lead met `source`/`source_url` vermeld.
- Geen scraping achter logins, geen CAPTCHA-omzeiling, geen verborgen persoonsgegevens,
  geen geraden persoonlijke e-mailadressen.
- Bedrijfsgegevens (naam/afzender/footer) overal configureerbaar.
- Per e-mail: `List-Unsubscribe`, duidelijke afzender, opt-out nodig om te stoppen.
- UNSUBSCRIBE/BOUNCE/SUPPRESSION stoppen autonome mail **permanent**.
- Auditlog per actie, zonder wachtwoorden of geheimen.

---

## Testen

    python -m pytest -q
    # end-to-end pipeline op een lokale sqlite-database:
    DATABASE_URL=sqlite:////tmp/rd.db python scripts/smoke_test.py

Gedekt: deduplicatie, suppression-list (een onderdrukt contact ontvangt nooit een
nieuwe mail), lead-kwalificatie, mailgeneratie, verzendlimieten, dry-run, follow-ups,
uitschrijven en bounce-afhandeling.

---

## Belangrijke instellingen

Raadpleeg `.env.example`. Belangrijkste:

| Variabele | Standaard | Betekenis |
|---|---|---|
| `DRY_RUN` | `true` | niets verzenden |
| `LIVE_SEND_ENABLED` | `false` | extra gate voor live verzenden |
| `ENABLE_AUTOMATION` | `false` | draait de pipeline in de worker |
| `DAILY_EMAIL_LIMIT` | `25` | max. mails per dag |
| `HOURLY_EMAIL_LIMIT` | `5` | max. mails per uur |
| `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | `60`/`180` | rustpauze tussen mails |
| `MIN_LEAD_SCORE` | `65` | minimum relevantiescore |
| `MAX_FOLLOWUPS` | `1` | maximum aantal vervolgmails |
| `SERVICE_REGIONS` | `Zuid-Holland` | servicegebied (zonder regio op campagne) |

Dienst-naar-branche-mappingen zijn bewerkbaar via het dashboard (Instellingen).

---

## Fasen van bouw

De eerste versie is gefaseerd gebouwd; iedere fase is getest, op fouten nagekeken,
op veiligheid gecontroleerd, en het bestaande werkt nog steeds:

1. Database + dashboard + handmatige leads
2. Automatische lead discovery
3. Website-analyse + zakelijke contactvinder
4. AI-kwalificatie + mailgeneratie
5. E-mail verzenden met DRY_RUN
6. Replies, bounces, suppression en follow-ups
7. Scheduler + automatisering
8. Tests, veiligheidsreview en deployment

---

## Grenzen van deze versie

- Discovery draait per campagne één keer per dag in Overpass; gebruik voor volume een
  eigen `DISCOVERY_API_URL` (openbare instanties zijn bedoeld voor licht gebruik).
- Inkomend verkeer wordt alleen via IMAP gelezen; provider-webhooks zijn nog niet gebouwd.
- De worker draait als één instantie. Zet voor meerdere replica's een gedeelde job-lock in.
