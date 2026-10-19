# Architecture and recommendation flow

## Components

```text
Browser
  │ HTTP/JSON
  ▼
React + Vite frontend
  │ /api
  ▼
FastAPI backend ───────────────► optional TMDB API
  │
  ├── SQLAlchemy / SQLite ─────► users, movies, ratings, recommendation records
  │
  └── recommender package ─────► persisted model artifacts
       ├── popularity
       ├── content similarity
       ├── PyTorch explicit matrix factorization
       └── hybrid ranking
```

The API is the boundary between presentation, persistence, and recommendation logic. SQLite makes
the default installation portable and inspectable. Model files live outside the database so they
can be rebuilt or replaced independently. Compose mounts both locations as persistent volumes.

## Offline path

1. Download a permitted MovieLens archive from GroupLens.
2. Validate and ingest movies, links, tags, and ratings into the local data layer.
3. Build the popularity and content features.
4. Split interactions without leaking held-out ratings into training.
5. Train explicit matrix factorization using PyTorch.
6. Evaluate each eligible model against the same split and record configuration, seed, dataset
   identity, and metrics.
7. Persist model artifacts and metadata for the online service.

MovieLens `latest-small` is the development default. MovieLens 32M is an opt-in workload with much
higher download, memory, training, and storage requirements.

## Online recommendation path

1. The client requests recommendations for a user.
2. The API loads that user's known ratings from SQLite.
3. For a new user, popularity recommendations are returned until enough preference signal exists.
4. For a known user, available candidate generators score movies:
   - popularity estimates broad appeal;
   - content similarity reflects genres and metadata;
   - matrix factorization estimates an explicit rating from learned latent factors.
5. The hybrid ranker combines configured, normalized scores. Missing model signals are skipped
   rather than represented as false confidence.
6. Movies already rated by the user are removed unless an endpoint explicitly requests otherwise.
7. The API returns ranked items with score/reason metadata suitable for display.

The exact hybrid weights, minimum-rating threshold, and candidate limits are configuration and
model-version concerns. They should be published with each evaluated artifact.

## Design choices

- **Local first:** the core works without a cloud account, external database, or paid API.
- **Baseline first:** popularity remains available when personalized artifacts are absent.
- **Separation of concerns:** HTTP handlers do not own training, and recommenders do not own UI.
- **Reproducibility:** Python dependencies are managed with `uv`; frontend dependencies use npm;
  generated artifacts are kept out of source control.
- **Graceful enrichment:** TMDB failures must not prevent MovieLens-based recommendations.

## Group ranking

Group candidates must be acceptable to every selected profile. CineMatch ranks the intersection
with `minimum participant score + mean participant score`: the minimum term is a least-misery
guard, while the mean rewards broad enthusiasm. Movies watched by any participant are excluded by
default; the request's `include_watched` toggle explicitly relaxes that rule.
- **SQLite trade-off:** excellent for a portfolio/demo workload, but not intended for concurrent
  write-heavy production traffic.

## Security and privacy boundary

CineMatch does not need real personal information. Demo users should be pseudonymous. A public
deployment should validate inputs, restrict CORS, avoid exposing the SQLite file or artifacts, use
TLS at the edge, apply rate limits, and run in read-only mode unless authenticated writes are
implemented.
