# Architecture and recommendation flow

## Components

```text
React/Vite UI ── HTTP/WebSocket ── FastAPI modular monolith
                                      │
                         PostgreSQL + pgvector
                                      │
               ┌──────────────────────┼──────────────────────┐
               │                      │                      │
         TMDB display ingest   recommendation serving   background CLI jobs
                                      │
               content + implicit ALS + popularity + novelty
                                      │
                              hybrid + MMR ranking
                                      │
                         optional VW ADF next-action policy
```

The application remains one deployable backend rather than a microservice graph. PostgreSQL owns
accounts, movies, embeddings, interactions, recommendation decisions, groups, votes, and audit
records. Versioned model files live in the artifact volume. Redis is optional and used only for a
shared multi-instance rate limit/cache.

## Data and identity boundaries

JWT access tokens are short-lived; rotated refresh tokens are HttpOnly and hashed at rest.
Profiles belong to users. A one-time claim endpoint migrates legacy browser-session profiles
without exposing their history. MovieLens users, application users, and group-session participants
have distinct namespaces.

TMDB metadata, images, and availability are display-only pending explicit ML permission.
Sentence Transformer input is restricted to ML-eligible MovieLens title/genre text by a
machine-readable policy. See `data-rights.md`.

## Personal serving path

1. Eligibility removes explicit rejections, watched items when requested, safety exclusions, and
   unavailable country/provider combinations before exploration.
2. Candidate sources contribute pgvector content similarity, implicit ALS, popularity, novelty,
   and cold-start exploration.
3. Source scores are normalized and combined; MMR controls redundant items.
4. A recommendation decision stores the eligible candidates, actual displayed items/positions,
   model/feature/policy/experiment versions, and real propensities when a policy selected an item.
5. VW ADF may select the next item only after shadow comparison. Failure falls back to hybrid.
6. Feedback references the exact request and impression. Organic, recommendation, and bandit
   actions remain separate; corrections prevent duplicate training signals.

## Group serving path

A session snapshots consenting participants. Its candidates are the union of each member's
personal candidates plus safe exploration, so minority tastes can enter the pool. Shared
availability/runtime/language/genre constraints apply before ranking. Member scores are normalized
and uncertainty-aware; average, least-misery, fair-consensus, and Nash algorithms are explicit.

Votes are session-scoped, one influence per participant. Veto removes a movie only from that
session. Membership changes version the participant set. WebSockets publish vote changes, and a
configured majority/strong/unanimous/veto policy controls finalization. Group feedback never
updates a personal profile implicitly.

## Offline and operations path

Chronological evaluation compares simple baselines before promotion. Data, feature, embedding,
model, policy, and experiment versions are traceable. LightGBM is data-gated. IPS/DR fails closed
without eligible sets and propensities. New models start in shadow mode and promotion/rollback
decisions are audited. Retraining is triggered by enough eligible data, measured degradation, or
drift rather than an arbitrary calendar.
