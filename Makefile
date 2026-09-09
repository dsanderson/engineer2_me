.PHONY: help dev runner test test-integration lint fmt up down logs seed reindex backup

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

dev:  ## run the app locally against ./data (no docker)
	uv run python main.py

runner:  ## run the calculator sandbox locally on :8001
	PYTHONPATH=. uv run python -m runner.server

test:  ## run the test suite
	uv run pytest -q

test-integration:  ## seed a running stack end to end (E2_BASE_URL, E2_PASSWORD)
	uv run python examples/seed_mission.py --base-url $${E2_BASE_URL:-http://localhost:8000}

lint:  ## check style
	uv run ruff check app runner tests examples skills main.py
	uv run ruff format --check app runner tests examples skills main.py

fmt:  ## format
	uv run ruff format app runner tests examples skills main.py
	uv run ruff check --fix app runner tests examples skills main.py

up:  ## build and start the docker stack (caddy + app + runner)
	cd deploy && docker compose up -d --build

down:  ## stop the docker stack
	cd deploy && docker compose down

logs:  ## follow the stack logs
	cd deploy && docker compose logs -f

seed:  ## build the worked example on a locally running app
	uv run python examples/seed_mission.py

reindex:  ## drop the index cache; it is rebuilt on next start
	rm -f $${E2_DATA_DIR:-./data}/index.json

backup:  ## tar the data directory
	tar czf engineer2-backup-$$(date +%Y%m%d-%H%M%S).tgz $${E2_DATA_DIR:-./data}
