# CineMatch project status

Last updated: 2026-10-19

## Improvement checklist

- [x] Stage 1: refresh MovieLens-to-TMDB metadata in existing databases, expose nullable
  `poster_url`, and render accessible lazy poster images with resilient fallbacks
- [x] Stage 2: persist explicit swipe preferences and connect them to personal recommendations
- [x] Stage 3: add owned profiles, invitations, membership controls, and fair group ranking
- [x] Verification and final documentation

## Current state

CineMatch now has session-owned profiles, persisted swipe state/history with idempotency and undo,
diversified onboarding, recommendation version invalidation, expiring group invitations, and
per-member normalized group ranking. Popularity remains the measured default because it led
NDCG@10 on the untouched MovieLens test split; the hybrid did not win.

## Verified commands

- `uv lock && uv sync --python 3.11`
- `python -m scripts.cli prepare --dataset latest-small`: 610 users, 9,742 movies, 100,836 ratings,
  fingerprint `f99049f327bee630ea3d2a7e5be3ec494be7199977593032da402d18d266ee19`
- `python -m scripts.cli evaluate`: four models, full catalogue, 363 evaluated positive-test users
- `alembic upgrade head`
- `alembic upgrade head`: clean migration and upgraded local database passed
- `pytest`: 15 passed (one upstream Starlette TestClient deprecation warning)
- `ruff check .`: passed
- `mypy backend recommender scripts`: passed (22 files)
- `npm run typecheck`: passed
- `npm run test`: 4 passed
- `npm run build`: passed
- Integration flow: two independently owned users, distinct feedback, invitation join, group
  ranking, version/cache invalidation, expiry, revocation, privacy, and score formulas passed

## Next commands

```bash
cp .env.example .env
uv sync --python 3.11
npm --prefix frontend ci
uv run --python 3.11 python -m scripts.cli prepare --dataset latest-small
uv run --python 3.11 alembic upgrade head
uv run --python 3.11 uvicorn backend.app:app --reload
# another terminal
npm --prefix frontend run dev
```

## Known limitations

- Docker/Compose runtime smoke was not run because Docker is not installed; configuration is present.
- Remote deployment was not performed because it requires explicit authorization.
- TMDB enrichment and real remote poster loading were not called because no token was supplied;
  mappings, backfill, URL generation, missing credentials, and nullable posters are tested.
- `npm run e2e`: 2 passed (desktop Chromium and mobile Chromium against the real backend)
- Session keys provide local owned sessions, not password recovery or cross-device accounts.
- Optional group shortlist voting is not implemented.
- SQLite is for local development. Public deployments need HTTPS, persistent storage, backups,
  `CINEMATCH_PUBLIC_APP_URL`, and a production identity strategy.
- Offline MovieLens metrics do not establish engagement, causality, or production-scale behavior.
- The current online path intentionally defaults to the measured popularity/content fallback;
  richer trained artifacts remain available for experimentation and evaluation.
