.PHONY: up up-gpu down logs dev seed evaluate evaluate-offline test lint frontend-build verify

COMPOSE_GPU = -f docker-compose.yml -f docker-compose.gpu.yml

up:
	docker compose up -d --build
up-gpu:
	docker compose $(COMPOSE_GPU) up -d --build
down:
	docker compose down
logs:
	docker compose logs -f backend
# Benchmark inside the running stack (local Ollama agent + local judge; no external API needed).
evaluate:
	docker compose exec backend python -m app.evaluation.run_benchmark --provider ollama --judge ollama
# Deterministic offline benchmark (demo agent, no LLM judge) for quick regression checks.
evaluate-offline:
	cd backend && PYTHONPATH=. LLM_PROVIDER=demo python -m app.evaluation.run_benchmark --judge none

dev:
	cd backend && PYTHONPATH=. uvicorn app.main:app --reload --port 8000
seed:
	cd backend && PYTHONPATH=. python scripts_seed.py
test:
	cd backend && PYTHONPATH=. pytest -q
lint:
	cd backend && ruff check app tests scripts
frontend-build:
	cd frontend && npm run build
verify:
	cd backend && PYTHONPATH=. python scripts/verify.py
