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
- VW is shadowed by default pending evidence against the hybrid baseline. LightGBM remains minimum-data gated.
- Remote deployment, production TLS, backups, and external legal review remain operator responsibilities.
