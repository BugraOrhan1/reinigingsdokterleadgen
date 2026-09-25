# Reinigingsdokter Outreach

Een FastAPI-dashboard en PostgreSQL-worker voor zakelijke leads, openbare contactadressen, gepersonaliseerde conceptmails, verzendlimieten en reactie-verwerking. **Standaard: `DRY_RUN=true`, `ENABLE_AUTOMATION=false` en `LIVE_SEND_ENABLED=false`.** Er wordt dan niets verzonden.

## Opstarten

1. Installeer Python 3.12, Docker Compose en Git op een ontwikkelmachine of VPS.
2. Kopieer `.env.example` naar `.env`. Kies een lang willekeurig `APP_SECRET` en `ADMIN_PASSWORD`; stel `POSTGRES_PASSWORD` in en gebruik exact dat wachtwoord in `DATABASE_URL`.
3. Start met `docker compose up --build -d`. De database-migratie loopt bij het opstarten. Open lokaal `http://127.0.0.1:8000` en log in met HTTP Basic.
4. Zet voor een VPS een HTTPS reverse proxy voor de app. De Compose-poort luistert alleen op localhost. Sla `.env` buiten versiebeheer op en maak databaseback-ups.
5. Test met `python -m pytest -q`. Controleer de auditlog, de gegenereerde mails en de ingestelde servicegebieden voor je automatisering aanzet.

## Werking

- Maak een campagne met branches, regio, scoregrens, daglimiet en aantal follow-ups. Koppel handmatige leads via de ID's op het campagnescherm.
- De worker zoekt maximaal eenmaal per campagne per dag in OpenStreetMap via Overpass, bezoekt hoogstens vijf openbare pagina's per bedrijfswebsite, leest algemene zakelijke contactadressen en berekent een score.
- De ingestelde mapping of de standaardmapping bepaalt de dienst. Zonder `OPENAI_API_KEY` maakt de applicatie een vaste Nederlandse variant. Met de API-sleutel kan de LLM een korte mail maken; bij een fout valt hij terug op de vaste variant.
- Conceptmails worden pas bij het verzenden gecontroleerd op blokkade, eerdere benadering, suppression, score, limieten en campagnestatus. Dry-run bewaart concepten zonder provider-aanroep.
- Inkomende reacties kunnen handmatig worden geregistreerd of via IMAP worden gelezen. Een opt-out en een bounce komen in de suppression list. Reacties stoppen vervolgberichten. Onzekere verzendingen worden nooit automatisch opnieuw geprobeerd.

## Live verzenden

Vul `COMPANY_ADDRESS`, `COMPANY_EMAIL`, SMTP-gegevens of Gmail OAuth client/refresh-token in. Verifieer eerst pipeline, deduplicatie, suppression en limiettests in jouw omgeving. Zet daarna `ENABLE_AUTOMATION=true`, `DRY_RUN=false` en `LIVE_SEND_ENABLED=true`. Begin met lage limieten. De applicatie kan ontvangst door de provider vaststellen; `delivered` vereist een afzonderlijke providergebeurtenis en blijft zonder die koppeling leeg.

Voor structurele discovery hoort een eigen of commerciële Overpass-instantie te worden gebruikt (`DISCOVERY_API_URL`). De openbare instantie is alleen geschikt voor klein gebruik. De bron is OpenStreetMap, beschikbaar onder ODbL; behoud bronvermelding en beoordeel de licentieplichten voor je toepassing. Websites worden alleen via gewone openbare pagina's gelezen, zonder login of CAPTCHA-omzeiling. Controleer ook je eigen grondslag en opt-outproces voor zakelijke acquisitie.

## Grenzen van deze eerste versie

- Zoekradius en bedrijfsnaamfilter zijn nog niet aangesloten op automatische discovery; een website kan handmatig als lead worden toegevoegd.
- Alleen e-mailreacties via IMAP worden ingelezen. Provider-webhooks voor afleverstatus ontbreken.
- Verzendinstellingen worden via `.env` beheerd; dienstmappings kunnen in het dashboard worden gewijzigd.
- De worker draait als één instantie. Gebruik geen meerdere worker-replica's zonder gedeelde job-lock.

Bronnen: [Overpass API usage](https://wiki.openstreetmap.org/wiki/Overpass_API), [OpenStreetMap licentie](https://osmfoundation.org/wiki/Licence_and_Legal_FAQ).
