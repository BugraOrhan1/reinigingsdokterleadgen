# Handige korte commando's
.PHONY: demo run test lint smoke

demo:            ## start met demo-data (./start.sh --demo)
	./start.sh --demo

run:             ## start zonder demo-data
	./start.sh

test:            ## draai alle tests
	.venv/bin/python -m pytest -q

smoke:           ## pipeline end-to-end op een tijdelijke sqlite-db
	DATABASE_URL=sqlite:////tmp/rd_smoke.db .venv/bin/python scripts/smoke_test.py

lint:            ## snelle code-check
	.venv/bin/ruff check app tests scripts --select F,E9
