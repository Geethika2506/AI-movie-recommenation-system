# Training, evaluation, and model operations

All commands are CPU-compatible on Apple Silicon with Python 3.11. Run migrations before jobs.
The default MovieLens dataset is approved only for development/research; see `data-rights.md`.

## Prepare data and embeddings

```bash
uv run python -m scripts.cli prepare --dataset latest-small
uv run alembic upgrade head
uv run python -m scripts.cli embed
```

The embedding job uses `sentence-transformers/all-MiniLM-L6-v2` on CPU and only MovieLens title
and genre text. TMDB overview, keywords, cast, crew, provider data, and images are display-only and
are deliberately absent from embedding input. Records include model, feature, source, and policy
fingerprints; unchanged records are skipped.

## Collaborative and benchmark models

```bash
uv run python -m scripts.cli train-collaborative
uv run python -m scripts.cli evaluate
uv run python -m scripts.cli train
```

Implicit ALS consumes corrected, non-undone application actions with configurable confidence.
Application and MovieLens users use `app:` and `movielens:` namespaces and can never collide.
Serving falls back to content/popularity when an artifact, user, or item is unavailable.

The benchmark performs chronological splits and compares popularity, content, MF, and hybrid
baselines using Precision/Recall/NDCG@10, coverage, diversity, novelty proxy, and latency. Do not
promote an advanced model unless it improves the declared objective or records an accepted,
measured trade-off. Synthetic group results must be labeled simulated.

## Contextual bandit

Set `CINEMATCH_BANDIT_ENABLED=true` to load VW ADF. It defaults to shadow mode:

```env
CINEMATCH_BANDIT_ENABLED=true
CINEMATCH_BANDIT_SHADOW=true
CINEMATCH_BANDIT_EPSILON=0.1
```

VW receives shared profile/session context and eligible action features after all safety and
availability filters. Shadow decisions do not log propensities as if they selected displayed
movies and cannot train the policy. After controlled comparison, set shadow false to permit the
policy to select the first movie. The exact eligible set, example lines, action, probability,
policy version, and impression are persisted. Valid feedback uses `cost = -reward`, applies once,
and atomically checkpoints `artifacts/vw/policy.model`.

Organic actions and historical records without an eligible set and propensity are excluded from
bandit learning and IPS/DR evaluation. A missing/corrupt VW policy falls back to the hybrid.

## Promotion, rollback, and retraining

Promotion requires chronological or valid logged-policy evaluation, latency/fallback guardrails,
and an auditable `model_promotions` decision. Deploy new policies in shadow mode first. Rollback
means restoring the last approved immutable artifact and recording why.

Retraining is triggered by enough new ML-eligible corrected events, measured quality degradation,
or feature drift. It is not scheduled merely because time passed. `/api/health/models` reports
embedding coverage, fallback rate, impressions, and artifact availability.

User deletion removes direct data through database cascades. Derived profile vectors disappear
with profiles; global artifacts that incorporated deleted data must be invalidated and retrained
according to the deployment retention policy.
