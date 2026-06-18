# Chorus — make dev / demo / test

PY ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

.PHONY: dev demo test clean

dev:  ## run the FastAPI app with reload (Qwen brain via .env or X-Qwen-Key)
	$(PY) -m uvicorn chorus.app:get_app --factory --reload --port 8002

demo:  ## serve Chorus's orchestration UI + API at :8002
	@test -f .env || (echo "→ creating .env from .env.example"; cp .env.example .env)
	@echo "→ Chorus at http://localhost:8002  (enter your Qwen key in the UI)"
	$(PY) -m uvicorn chorus.app:get_app --factory --host 0.0.0.0 --port 8002

test:
	$(PY) -m pytest

clean:
	rm -rf .pytest_cache engine/__pycache__ chorus/__pycache__ engine/tests/__pycache__
