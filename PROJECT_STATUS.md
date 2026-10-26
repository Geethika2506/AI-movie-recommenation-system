# CineMatch project status

Last verified: 2026-10-26

## Implemented

- Phase 0: rights/provenance policy, ML eligibility, event and feature contracts, objectives, and parity tests.
- Phase 1: PostgreSQL/pgvector, JWT authentication, profile claiming, TMDB catalog ingestion, full interaction semantics, and responsive React discovery.
- Phase 2: permitted-field embeddings, taste profiles, cold start, hybrid/diverse ranking, attribution, and personal recommendation APIs.
- Phase 3: consent-aware groups, invitations, sessions, fair ranking, isolated voting, real-time consensus, finalization, feedback, and group UI.
- Phase 4: implicit ALS, hybrid serving, gated LightGBM, VW ADF bandits with valid logging/checkpoints, rollback, and off-policy safeguards.
- Phase 5: structured logs, security controls, optional Redis limits, diagnostics, experiment/promotion controls, deletion, CI, CPU-safe Docker, Compose, and documentation.

## Verification

- `pytest`: 37 passed.
- Ruff passed; mypy passed for 41 source files.
- Frontend Vitest: 4 passed; typecheck and production build passed.
- Alembic upgrade/downgrade passed on SQLite.
- Alembic upgrade and authenticated API smoke passed on PostgreSQL 17 with pgvector.
- Compose validates; deployment image build passed.
- Automated multi-user invitation, voting, ranking, and final-selection flow passed.

## Operational boundaries

- TMDB fields are display-only for persistent ML features under the documented public terms. Permitted MovieLens title/genre fields supply offline ML inputs.
- Live TMDB was not called because `CINEMATCH_TMDB_TOKEN` is unconfigured; mocked ingestion, missing credentials, provenance, and field-policy enforcement are tested.
- LightGBM remains minimum-data gated.
- Remote deployment, production TLS, backups, and external legal review remain operator responsibilities.

## Vowpal Wabbit (contextual bandit)

**Implemented in code (Phase 4), not enabled on live Render by default.**

| Piece | Status |
|-------|--------|
| `VWPolicy` (ADF, epsilon-greedy) | In `recommender/contextual_bandit.py`; unit tests in `tests/test_advanced_ml.py` |
| Personal feed integration | After hybrid pool is built; optional reorder when shadow is off |
| Impression logging | `policy_context`, shadow flag, propensities when not in shadow |
| Feedback → learn | `origin=bandit_policy` with impression linkage; checkpoint at `artifacts/vw/policy.model` |
| Health signal | `GET /api/health/models` → `bandit_available` |

**Production defaults** (see `.env.example`, `backend/config.py`; `render.yaml` does not override):

- `CINEMATCH_BANDIT_ENABLED=false` — VW is **not loaded** at API startup on the public deploy.
- `CINEMATCH_BANDIT_SHADOW=true` — when enabled, users still see the **hybrid** list; VW runs **counterfactual** choices for logging only.

**Why it stays off in production (talking points):**

1. **Evidence** — Offline MovieLens evaluation documented **popularity/hybrid** as the baseline; bandits are not promoted without shadow or controlled comparison data.
2. **ML contracts** — Bandit training and off-policy evaluation require logged propensities, eligible sets, and impression-linked feedback (`docs/ml-contracts.md`). Shadow mode avoids treating counterfactual picks as displayed actions.
3. **Scope** — The shipped product story is hybrid personal ranking plus group fairness; VW is optional exploration infrastructure, not the primary demo path.
4. **Hosting** — Render’s ephemeral filesystem drops `artifacts/vw/` on redeploy unless persistent storage or an external artifact store is added; default-on would imply durable learning the free tier does not provide.

**Enabling safely (operator):** set `CINEMATCH_BANDIT_ENABLED=true`, keep `CINEMATCH_BANDIT_SHADOW=true`, redeploy API, confirm `bandit_available: true` on `/api/health/models`. Only set `CINEMATCH_BANDIT_SHADOW=false` after shadow analysis and an audited promotion (see `docs/training.md`).
