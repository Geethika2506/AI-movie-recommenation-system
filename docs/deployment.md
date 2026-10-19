# Deployment guide

## Local processes

Install exactly from the project manifests:

```bash
cp .env.example .env
uv sync --python 3.11
npm --prefix frontend install
```

Run the API and Vite development server in separate terminals:

```bash
uv run --python 3.11 uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
CINEMATCH_BACKEND_URL=http://127.0.0.1:8000 npm --prefix frontend run dev
```

For reproducible CI after lockfiles exist, use `uv sync --frozen --python 3.11` and `npm ci` rather
than updating dependency resolution.

## Docker Compose

```bash
cp .env.example .env
docker compose build
docker compose up
```

The frontend is exposed at <http://localhost:5173> and the API at
<http://localhost:8000>. Named volumes persist:

- `/app/data`: SQLite and downloaded/processed local data;
- `/app/artifacts`: trained model files and evaluation artifacts.

`docker compose down` retains them. `docker compose down --volumes` permanently deletes them.

Compose is a zero-license-cost local topology, not a claim that compute, storage, bandwidth, TMDB,
or a hosted Docker service is free.

## Environment

Copy `.env.example` and review every value:

- `CINEMATCH_DATABASE_URL`: SQLite URL; Compose uses the persistent `/app/data` path.
- `CINEMATCH_DATA_DIR`: downloaded and processed data directory.
- `CINEMATCH_ARTIFACT_DIR`: trained artifact directory.
- `CINEMATCH_CORS_ORIGINS`: comma-separated trusted frontend origins.
- `CINEMATCH_READ_ONLY`: disable mutations for a public demo.
- `CINEMATCH_TMDB_TOKEN`: optional secret; do not bake it into an image.
- `VITE_API_URL`: frontend API base embedded at frontend build time.

Back up the database and matching artifacts together. SQLite backup/copy operations must account
for active writes; stop the service or use SQLite's supported online backup mechanism.

## Current free-hosting reality

There is no universally suitable, permanent “free” production host for this architecture. Free
tiers change, may require a card, sleep after inactivity, cap CPU/RAM/build minutes/egress, prohibit
background training, and can be removed without notice. PyTorch also makes images and memory use
larger than a minimal web API.

Many free app hosts provide an **ephemeral filesystem**. On such a host, SQLite writes, downloaded
MovieLens data, and trained artifacts disappear on restart or redeploy. A read-only image can ship
small, license-permitted prebuilt state, but do not bake MovieLens data or derived artifacts into a
public image until redistribution rights are confirmed. A persistent disk may be paid and is often
single-instance, while object storage does not directly replace SQLite.

Practical portfolio options:

1. Run the full system locally with Compose during a live demo.
2. Deploy only the static frontend with clearly labeled fallback/sample content.
3. Host a read-only API with `CINEMATCH_READ_ONLY=true`, pre-provisioned state, persistent storage,
   strict CORS, and no in-host training.
4. Move to a managed database/object store before enabling public writes or horizontal replicas.

Always verify a provider's current pricing and terms at deployment time. This repository does not
claim deployment to, compatibility with, or ongoing free eligibility on any named provider.

## Production hardening checklist

- Pin and scan base images and dependencies.
- Terminate TLS at a trusted proxy and set explicit CORS origins.
- Run as a non-root user with a read-only root filesystem where feasible.
- Keep tokens in the host's secret manager.
- Add authentication and authorization before enabling public writes.
- Add rate limits, request-size limits, structured logs, health checks, and backups.
- Use a production database for concurrent writers and multiple API replicas.
- Train offline; promote validated, versioned artifacts atomically.
- Confirm GroupLens and TMDB terms for the exact public use.
