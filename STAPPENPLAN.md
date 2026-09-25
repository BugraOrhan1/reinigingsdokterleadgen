# Zo werkt het — in gewone taal

## 1. Starten (klaar in 1 minuut)

```bash
./start.sh --demo
```

Wat er dan gebeurt:
- er wordt automatisch een `.env` aangemaakt met een veilig wachtwoord;
- het dashboard start met **voorbeeldbedrijven**, zodat je meteen ziet hoe alles eruitziet;
- in de terminal staat je inlog: `admin / <wachtwoord>`.

Open daarna **http://127.0.0.1:8000** en log in.

> Geen Docker op je computer? Geen probleem — `start.sh` start dan automatisch
> met een lokale SQLite-database. Voor een VPS gebruik je Docker (`docker compose up -d`).

## 2. Wat je op het dashboard ziet

| Pagina | Wat je doet |
|---|---|
| **Overview** | alle tellers: leads, mails, geïnteresseerde leads, opt-outs |
| **Leads** | bedrijven toevoegen of bekijken |
| **Campaigns** | campagne maken (branche + regio + limieten) en starten/pauzeren |
| **Emails** | alle gegenereerde en verstuurde mails |
| **Replies** | binnengekomen reacties (of zelf invoeren) |
| **Interested Leads** | de bedrijven die interesse hebben getoond |
| **Suppression List** | adressen/domeinen die we nooit meer mailen |
| **Settings** | welke reinigingsdienst bij welke branche hoort |
| **Logs** | alles wat er is gebeurd (auditlog) |

## 3. Zo zet je ECHTE e-mails aan (stap voor stap)

**Belangrijk:** standaard staat alles op "droog oefenen" (`DRY_RUN=true`) en wordt er
**niets** verstuurd. Pas als je onderstaande stappen hebt gedaan, gaan er echte mails uit.

1. **Eerst testen (droog):** maak een campagne en start hem. Je ziet in *Emails*
   dat er mails worden klaargezet met status `draft` en uitkomst `dry_run` — er is
   niets verzonden.
2. **SMTP-gegevens invullen in `.env`:**
   ```ini
   COMPANY_ADDRESS=Jouwstraat 1, 1234 AB Amsterdam
   COMPANY_EMAIL=info@reinigingsdokter.nl
   SMTP_HOST=smtp.jouwprovider.nl
   SMTP_PORT=587
   SMTP_USERNAME=info@reinigingsdokter.nl
   SMTP_PASSWORD=je-wachtwoord
   SMTP_FROM=Reinigingsdokter <info@reinigingsdokter.nl>
   ```
   (of gebruik Gmail: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`)
3. **De schakelaars omzetten in `.env`:**
   ```ini
   DRY_RUN=false
   LIVE_SEND_ENABLED=true
   ENABLE_AUTOMATION=true
   ```
4. **Limieten laag beginnen** (staan al laag in `.env`):
   ```ini
   DAILY_EMAIL_LIMIT=25
   HOURLY_EMAIL_LIMIT=5
   MIN_DELAY_SECONDS=60
   MAX_DELAY_SECONDS=180
   ```
5. **Herstart** (`./start.sh`) en start je campagne. Vanaf nu zoekt het systeem
   zelf bedrijven (OpenStreetMap), analyseert hun website, vindt hun openbare
   `info@`/`contact@`-adres, maakt een persoonlijke mail en verstuurt die binnen
   de ingestelde limieten.

**Wat er standaard al goed geregeld is:**
- geen e-mail naar iemand die is uitgeschreven of al is benaderd;
- bounces en uitschrijvingen komen op de uitsluitingslijst;
- mails hebben een afmeld-link en nette afzendergegevens;
- alles wordt gelogd (zonder wachtwoorden).

## 4. Handige korte commando's

```bash
make test     # alle tests
make smoke    # hele pipeline nagelopen op een tijdelijke database
make lint     # code-check
```

## 5. Veelgestelde vragen

**Ik heb geen SMTP-server.** Gebruik een bekende e-mailprovider (bijv. TransIP,
Zoho, Google Workspace) die SMTP ondersteunt, of vraag je hostingpartij. Voor een
proef kun je de Gmail-route gebruiken.

**Hoe kom ik van de demo-data af?** Verwijder `data/app.db` (lokaal) en start
opnieuw met `./start.sh` (zonder `--demo`), of `docker compose down -v` voor Docker.

**Waar staat mijn wachtwoord?** In `.env` onder `ADMIN_PASSWORD` (verander het
naar iets wat je zelf kiest).
