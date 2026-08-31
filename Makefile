.PHONY: up down status logs bootstrap download ingest verify stage1 process-discover train-model stage2 test test-unit lint frontend-test smoke monitoring clean-containers

up:
	docker compose up -d --build --wait

down:
	docker compose down

status:
	docker compose ps

logs:
	docker compose logs --tail=200 api worker bootstrap

bootstrap:
	docker compose up -d postgres
	docker compose run --rm backend alembic upgrade head

download:
	docker compose run --rm backend decisionflow download

ingest:
	docker compose run --rm backend decisionflow ingest

verify:
	docker compose run --rm backend decisionflow verify

stage1: bootstrap download ingest verify

process-discover:
	docker compose run --rm backend decisionflow process-discover

train-model:
	docker compose up -d mlflow
	docker compose run --rm backend decisionflow train-model

stage2: process-discover train-model

test:
	docker compose build backend
	docker compose up -d --wait postgres temporal
	docker run --rm --network decisionflow_default -e PYTHONPATH=/app/backend/src -e TEST_DATABASE_URL=postgresql+psycopg://decisionflow:decisionflow@postgres:5432/decisionflow_test -e TEMPORAL_TEST_ADDRESS=temporal:7233 -v "$(CURDIR)/backend:/app/backend" -w /app/backend decisionflow-backend pytest --cov=decisionflow --cov-report=term-missing -q

test-unit:
	docker compose build backend
	docker run --rm -e PYTHONPATH=/app/backend/src -v "$(CURDIR)/backend:/app/backend" -w /app/backend decisionflow-backend pytest -m "not integration" -q

lint:
	docker compose build backend
	docker run --rm -v "$(CURDIR)/backend:/app/backend" -w /app/backend decisionflow-backend ruff check .
	docker run --rm -v "$(CURDIR)/backend:/app/backend" -w /app/backend decisionflow-backend mypy src

frontend-test:
	cd frontend && npm ci && npm audit --audit-level=high && npm run typecheck && npm test && npm run build

smoke:
	python3 scripts/smoke_demo.py

monitoring:
	docker compose --profile monitoring up -d prometheus

clean-containers:
	docker compose down
