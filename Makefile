PYTHON ?= $(shell command -v python3.12 || command -v python3)

.PHONY: setup backend-setup frontend-setup backend-test frontend-test frontend-build test docker-up docker-down

setup: backend-setup frontend-setup

backend-setup:
	cd backend && $(PYTHON) -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt

frontend-setup:
	cd frontend && npm ci

backend-test:
	cd backend && .venv/bin/python -m pytest -q

frontend-test:
	cd frontend && npm test -- --run

frontend-build:
	cd frontend && npm run build

test: backend-test frontend-test

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down
