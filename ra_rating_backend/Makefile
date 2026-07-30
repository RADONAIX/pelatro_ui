.PHONY: install dev migrate seed test lint fmt check

VENV ?= venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

install:
	python3.12 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]"

dev:
	$(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

migrate:
	$(VENV)/bin/alembic upgrade head

revision:
	$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

seed:
	$(PY) -m app.seed

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff check --fix .

check: lint test
