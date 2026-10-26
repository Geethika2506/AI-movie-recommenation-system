.PHONY: install prepare embed train train-collaborative evaluate migrate dev dev-backend dev-frontend demo-read-only test e2e check build compose-up compose-down clean

PYTHON := 3.11
UV_RUN := uv run --python $(PYTHON)

install:
	uv sync --python $(PYTHON)
	npm --prefix frontend ci

prepare:
	$(UV_RUN) python -m scripts.cli prepare --dataset latest-small

embed:
	$(UV_RUN) python -m scripts.cli embed

train:
	$(UV_RUN) python -m scripts.cli train

train-collaborative:
	$(UV_RUN) python -m scripts.cli train-collaborative

evaluate:
	$(UV_RUN) python -m scripts.cli evaluate

migrate:
	$(UV_RUN) alembic upgrade head

dev:
	@printf '%s\n' "Run 'make dev-backend' and 'make dev-frontend' in separate terminals."

dev-backend:
	$(UV_RUN) uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	CINEMATCH_BACKEND_URL=http://127.0.0.1:8000 npm --prefix frontend run dev

demo-read-only:
	CINEMATCH_READ_ONLY=true $(UV_RUN) uvicorn backend.app:app --host 127.0.0.1 --port 8000

test:
	$(UV_RUN) pytest
	npm --prefix frontend run test

e2e:
	CINEMATCH_BACKEND_URL=http://127.0.0.1:8000 npm --prefix frontend run e2e

check:
	$(UV_RUN) ruff check .
	$(UV_RUN) mypy backend recommender
	npm --prefix frontend run typecheck
	npm --prefix frontend run build

build:
	npm --prefix frontend run build
	docker compose build

compose-up:
	docker compose up --build

compose-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov frontend/dist frontend/node_modules
