# API guide

The generated OpenAPI document at `/docs` is authoritative. All account and recommendation writes
use `Authorization: Bearer <access-token>`; the refresh token is a secure HttpOnly cookie.

## Current platform endpoints

- `POST /api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`
- `GET/PATCH/DELETE /api/users/me`
- `POST /api/users/me/claim-legacy-profile`
- `GET /api/recommendations/personal`
- `GET /api/users/me/preferences`, `/api/users/me/wishlist`
- `POST /api/users/me/recommendations/reset`
- `POST /api/groups`, `POST /api/groups/{id}/invites`, `POST /api/group-invites/{token}/join`
- `POST/GET /api/groups/{id}/sessions`
- `POST /api/sessions/{id}/votes`, `GET /api/sessions/{id}/recommendations`
- `POST /api/sessions/{id}/finalize`, `POST /api/sessions/{id}/satisfaction`
- `WS /api/sessions/{id}/ws`
- `GET /api/health/models`

Recommendation rows include request/impression IDs and model, feature, and policy versions.
Recommendation-driven feedback must return those IDs; the API rejects mismatched attribution.

The FastAPI application is served at `http://localhost:8000`; application routes use the `/api`
prefix. The generated OpenAPI document at `/openapi.json` and interactive UI at `/docs` are the
authoritative contract for the checked-out version.

## Start and inspect

```bash
uv run --python 3.11 uvicorn backend.app:app --reload
curl --fail http://localhost:8000/health
open http://localhost:8000/docs
```

## Routes

- `GET /health` and `GET /ready`: process and database readiness.
- `POST /api/profiles`, `GET /api/profiles`: create and list local profiles.
- `GET /api/movies`: paginated search with `q`, `genre`, `year`, `page`, and `page_size`.
- `GET /api/movies/{movie_id}`: movie details.
- `PUT /api/profiles/{profile_id}/ratings/{movie_id}`: create or replace a rating.
- `PUT /api/profiles/{profile_id}/interactions/{kind}/{movie_id}`: activate/deactivate an
  interaction such as seen/watchlist, using the enum documented by OpenAPI.
- `GET /api/profiles/{profile_id}/interactions/{kind}`: list interaction movies.
- `GET /api/profiles/{profile_id}/recommendations?limit=20`: personalized or fallback ranking.
- `GET /api/movies/{movie_id}/similar?limit=20`: content-neighbor ranking.
- `POST /api/recommendations/group`: balanced ranking for two or more distinct profiles.
- `GET /api/models`: recorded model runs, parameters, fingerprints, and measured metrics.
- `GET /api/settings`: active measured model, dataset, catalogue counts, TMDB, and access mode.

## Contract conventions

- Requests and responses are JSON unless an import endpoint explicitly documents a file upload.
- Validation errors use FastAPI's standard `422` response.
- Missing resources return `404`; write attempts in read-only mode should return `403`.
- Movie and profile IDs are stable integer identifiers in the backend data store.
- Pagination, query limits, and rating bounds are enforced server-side.
- Recommendation responses contain the movie, a ranking score, and a human-readable reason.
  Scores from different model versions are not guaranteed to be directly comparable.

## Example workflow

```bash
profile_json=$(curl --fail --request POST http://localhost:8000/api/profiles \
  --header 'Content-Type: application/json' \
  --data '{"name":"Demo viewer"}')

# Substitute IDs returned by profile/movie requests.
curl --fail --request PUT http://localhost:8000/api/profiles/1/ratings/1 \
  --header 'Content-Type: application/json' \
  --data '{"value":4.5}'

curl --fail 'http://localhost:8000/api/profiles/1/recommendations?limit=10'

curl --fail --request POST http://localhost:8000/api/recommendations/group \
  --header 'Content-Type: application/json' \
  --data '{"profile_ids":[1,2],"limit":10}'
```

The unused `profile_json` variable intentionally preserves the response for inspection. Replace
example IDs with records present in the running database. New profiles may receive popularity
results until enough ratings exist.

## Read-only demonstrations

Set `CINEMATCH_READ_ONLY=true` for a public demo. Browsing, model metadata, and recommendations can
remain available, while profile enumeration and all mutations are rejected to avoid exposing
private local feedback. The frontend may
also use bundled demo data when configured to do so; fallback content must be labeled and must not
be represented as a live model result.

## Client configuration

For local Vite development, `/api` is proxied to `CINEMATCH_BACKEND_URL` (default
`http://127.0.0.1:8000`). `VITE_API_URL` can point a built frontend to another API origin. If the
origins differ, set `CINEMATCH_CORS_ORIGINS` to the exact allowed frontend origin rather than `*`.
