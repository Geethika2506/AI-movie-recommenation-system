# CineMatch

CineMatch is an end-to-end movie discovery platform. It combines a FastAPI API, React/Vite UI,
PostgreSQL/pgvector, JWT accounts, Sentence Transformer content embeddings, implicit ALS,
diversity-aware hybrid ranking, Vowpal Wabbit contextual bandits, and fair group sessions.

The project defaults to GroupLens MovieLens `latest-small`. MovieLens 32M can be selected for a
larger experiment, and TMDB enrichment is optional. Core recommendation flows do not require a
paid service or a TMDB account.

> The reproducible latest-small benchmark was run on 2026-10-19. Popularity was selected as the
> default because it led test NDCG@10; see the case study for measured results and limitations.

## Quick start

Prerequisites:

- Python 3.11
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm
- PostgreSQL 17 with pgvector, or Docker Desktop

```bash
cp .env.example .env
docker compose up -d db
uv sync --python 3.11
uv run --python 3.11 python -m scripts.cli prepare --dataset latest-small
uv run --python 3.11 alembic upgrade head
uv run --python 3.11 uvicorn backend.app:app --reload
```

In a second terminal:

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open <http://localhost:5173>. FastAPI's interactive API documentation is available at
<http://localhost:8000/docs>.

The intended verification commands are:

```bash
uv run --python 3.11 pytest
uv run --python 3.11 ruff check .
uv run --python 3.11 mypy backend recommender
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

Prepare the default data, evaluate the popularity baseline, and train explicit matrix
factorization:

```bash
uv run --python 3.11 python -m scripts.cli prepare --dataset latest-small
uv run --python 3.11 python -m scripts.cli evaluate
uv run --python 3.11 python -m scripts.cli train
```

Optional TMDB enrichment is resumable and is skipped safely without credentials:

```bash
export CINEMATCH_TMDB_TOKEN="your-read-access-token"
uv run --python 3.11 python -m scripts.cli enrich
# Restart the backend so cached metadata is synchronized into PostgreSQL.
uv run --python 3.11 uvicorn backend.app:app --reload
```

To pull **popular recent releases** (not in MovieLens) into the catalog:

```bash
uv run python -m scripts.cli import-tmdb-discover --years 3 --limit 500
uv run python -m scripts.cli embed --force
```

`CINEMATCH_TMDB_TOKEN` must be a TMDB API Read Access Token. It is used only by the Python
enrichment command and is never sent to the browser. Enrichment follows the MovieLens
`links.csv` MovieLens-to-TMDB IDs; it does not guess from titles. Requests have a five-second
timeout, bounded exponential retries, 429 handling, a disk cache, and resumable JSONL output.

The original missing-poster issue had two causes: starting the API before enrichment created
movies with null `poster_path`, and later startup skipped the entire import because rows already
existed; the UI also overlaid placeholder text on loaded background images. Startup now
synchronizes cached TMDB metadata into existing rows, the API emits nullable `poster_url`, and the
UI uses lazy `<img>` elements with fixed 2:3 sizing and an error fallback. Without credentials the
fallback artwork remains fully usable. Real remote images have not been verified in this checkout
because no token was supplied.

MovieLens 32M is opt-in because it needs substantially more time, memory, and disk:

```bash
uv run --python 3.11 python -m scripts.cli prepare --dataset 32m
uv run --python 3.11 python -m scripts.cli train \
  --data-dir data/processed/32m \
  --artifact-dir artifacts/mf-32m-v1
```

Inspect all supported options with `uv run --python 3.11 python -m scripts.cli --help`.

## Local Docker deployment

Docker Compose runs PostgreSQL/pgvector, the API, migrations, catalog preparation, and the frontend
with persistent database and model volumes:

```bash
cp .env.example .env
docker compose up --build
```

Then open <http://localhost:5173>. Stop services with `docker compose down`; add `--volumes` only
when you intentionally want to delete local database and model state.

Equivalent Make targets are available:

```bash
make install
make dev-backend
make dev-frontend
make test
make compose-up
```

Start a browse-only public-demo process with `make demo-read-only`; profile enumeration and all
writes are disabled in that mode.

## How recommendations flow

1. Licensed MovieLens titles/genres and ratings are ingested with provenance into PostgreSQL.
2. The popularity baseline supplies robust defaults for unknown or new users.
3. Sentence Transformer embeddings use only ML-eligible title/genre text and are stored in pgvector.
4. Implicit ALS learns confidence-weighted collaborative signals in isolated user namespaces.
5. Hybrid/MMR ranking combines content, collaborative, popularity, novelty, and safe exploration.
6. Optional VW ADF selects the next eligible movie with logged propensities and exact attribution.
7. Group sessions union every member's candidates and optimize mean/minimum satisfaction fairly.

### Swipe feedback

The **Discover** screen keeps meanings separate:

- right / Like and left / Dislike are explicit preference signals;
- Love is a stronger positive preference;
- Watched changes history only and does not imply enjoyment;
- Skip creates no negative label;
- Wishlist is intent to watch, not proof of enjoyment.

Updates are idempotent, owned by JWT-authenticated accounts, and recorded with previous/new snapshots.
Undo restores only the latest persisted action. Watched movies are excluded. Explicit states
update the genre profile without retraining the global model; recommendation and group profile
versions are incremented to invalidate dependent results. Existing numeric ratings remain intact:
ratings >= 4 are positive, <= 2.5 are negative, and 3–3.5 are neutral. No synthetic star rating is
invented for a swipe.

Cold-start ranking uses a 20-vote regularized MovieLens popularity prior. Personal ranking blends
that baseline with an average per-genre profile built from ratings, likes, dislikes, and a 0.2
watchlist signal. The onboarding pool round-robins genre/decade buckets to avoid repeating only
the first emerging preference. Offline TF-IDF and PyTorch matrix-factorization evaluation remain
on the dedicated Evaluation page; validation chooses benchmark hybrid weights, and this project
does not claim that MF/ALS beats the measured popularity baseline.
Compatibility was checked against the official
[scikit-learn 1.9.1 TF-IDF documentation](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)
and [PyTorch serialization documentation](https://docs.pytorch.org/docs/stable/notes/serialization.html);
the implementation uses normalized sparse TF-IDF vectors and CPU `state_dict` loading with
`weights_only=True`.

### Invitations and group ranking

Groups contain profiles owned by separate accounts. An owner or member can generate an
unguessable 256-bit invitation token that expires after 48 hours; owners can revoke invites.
Duplicate joins are idempotent, members can leave, owners can remove members, and group responses
only expose member names/onboarding counts—not ratings or swipe states.

Each consenting member contributes candidates before shared availability constraints. Scores are
normalized per member so larger histories cannot dominate. Active movie-night sessions keep votes
separate from personal history, support vetoes and live WebSocket updates, and use configurable
average, least-misery, fair-consensus, or Nash ranking.

- **Balanced:** `0.75 × avg + 0.25 × min`
- **Everyone matters:** `0.25 × avg + 0.75 × min`
- API custom baseline: `w × avg + (1-w) × min`

Unknown preferences use the popularity baseline rather than becoming dislikes. Movies watched by
any member are excluded by default. Genre, language, runtime, and rewatch constraints are applied
before aggregation. Large disagreement is labeled “Best available compromise.”

Set `CINEMATCH_PUBLIC_APP_URL` to the deployed frontend origin before sharing invitation links.
`localhost` works only for local testing: open a private/incognito browser (or a second browser),
create user B there, paste user A’s invitation URL, join, add swipe feedback, and return to the
group page. External deployment is intentionally not performed by this repository.

See [architecture](docs/architecture.md) and the
[recommender model card](docs/recommender-model-card.md) for details and limitations.

## Data and attribution

MovieLens datasets are provided by [GroupLens Research](https://grouplens.org/datasets/movielens/).
The default dataset is [MovieLens latest-small](https://files.grouplens.org/datasets/movielens/ml-latest-small-README.html);
[MovieLens 32M](https://grouplens.org/datasets/movielens/32m/) is optional.

MovieLens data is separate from this project's MIT-licensed code. Use of MovieLens is subject to
the terms in the dataset README and the GroupLens usage requirements, including attribution and
restrictions on redistribution and commercial use. Review those terms before publishing data,
artifacts derived from it, or a hosted service. Optional metadata may come from
[TMDB](https://www.themoviedb.org/); this product is not endorsed or certified by TMDB and use is
subject to the [TMDB API terms](https://www.themoviedb.org/documentation/api/terms-of-use).

More detail: [data provenance and terms](docs/data.md).
The stricter ML eligibility matrix is in [data rights](docs/data-rights.md), and versioned event,
feature, reward, and promotion rules are in [ML contracts](docs/ml-contracts.md).

## Documentation

- [Architecture and recommendation flow](docs/architecture.md)
- [Data provenance and licenses](docs/data.md)
- [Data rights and ML eligibility](docs/data-rights.md)
- [ML event and feature contracts](docs/ml-contracts.md)
- [Training, evaluation, and model operations](docs/training.md)
- [Recommender model card](docs/recommender-model-card.md)
- [API guide](docs/api.md)
- [Deployment guide](docs/deployment.md)
- [Portfolio case study](docs/portfolio-case-study.md)
- [Three-minute demo script](docs/demo-script.md)

## License

Source code and project-authored documentation are licensed under the [MIT License](LICENSE).
Third-party datasets, model inputs, packages, and TMDB content retain their own terms.
