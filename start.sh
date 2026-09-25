#!/usr/bin/env bash
# ============================================================
# Reinigingsdokter — start alles met één commando
#   ./start.sh          → start (Docker als beschikbaar, anders lokaal)
#   ./start.sh --demo   → + plaats demo-data
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

DEMO=0
[ "${1:-}" = "--demo" ] && DEMO=1

rand_hex() {
  python3 -c "import secrets; print(secrets.token_hex($1))" 2>/dev/null \
    || openssl rand -hex "$1" 2>/dev/null \
    || head -c $(( "$1" * 2 )) /dev/urandom | od -An -tx1 | tr -d ' \n'
}

# 1) Maak .env aan als die nog niet bestaat (veilige standaardwaarden).
if [ ! -f .env ]; then
  SECRET=$(rand_hex 32); APASS=$(rand_hex 6); DBPASS=$(rand_hex 16)
  cat > .env <<ENV_END
DATABASE_URL=postgresql+psycopg://outreach:$DBPASS@localhost:5432/outreach
POSTGRES_PASSWORD=$DBPASS
APP_SECRET=$SECRET
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$APASS
COMPANY_NAME=Reinigingsdokter
COMPANY_WEBSITE=https://www.reinigingsdokter.nl
COMPANY_ADDRESS=
COMPANY_EMAIL=
DRY_RUN=true
LIVE_SEND_ENABLED=false
ENABLE_AUTOMATION=false
DAILY_EMAIL_LIMIT=25
HOURLY_EMAIL_LIMIT=5
MIN_DELAY_SECONDS=60
MAX_DELAY_SECONDS=180
MAX_FOLLOWUPS=1
MIN_LEAD_SCORE=65
SERVICE_REGIONS=Zuid-Holland
DISCOVERY_API_TERMS_ACCEPTED=true
ENV_END
  echo "Nieuwe .env aangemaakt."
fi

set -a; . ./.env; set +a

echo ""
echo "  Reinigingsdokter · inlog:  ${ADMIN_USERNAME:-admin} / ${ADMIN_PASSWORD}"
echo "  (bewaar dit wachtwoord; het staat in .env)"
echo ""

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "Docker gevonden: opstarten via docker compose..."
  docker compose up --build -d
  echo ""
  echo "  Dashboard : http://127.0.0.1:8000"
  echo "  Logs      : docker compose logs -f"
  echo "  Stoppen   : docker compose down"
else
  echo "Geen Docker: lokale opstart met SQLite..."
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  export DATABASE_URL="sqlite:///$(pwd)/data/app.db"
  mkdir -p data
  .venv/bin/alembic upgrade head >/dev/null
  if [ "$DEMO" = "1" ]; then .venv/bin/python scripts/seed_demo.py; fi
  echo "Worker (scheduler) op de achtergrond..."
  .venv/bin/python -m app.workers.scheduler &
  echo ""
  echo "  Dashboard : http://127.0.0.1:8000"
  echo "  Stoppen   : Ctrl+C"
  exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
