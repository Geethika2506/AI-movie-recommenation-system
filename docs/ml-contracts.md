# Recommendation objectives and ML contracts

## Product contexts

Personal recommendations optimize useful discovery for one user from long-term, correctable
evidence. Group-session recommendations optimize a fair compromise for the currently participating
members from calibrated personal estimates and temporary session votes. Trending is a labeled
non-personal popularity feed. These contexts have separate request IDs, events, objectives, and
state; an action never changes context implicitly.

## Event sequence

1. A recommendation request records context, identity/session, constraints, and all versions.
2. Candidate generation records the complete eligible and rejected action set with reasons.
3. An impression records only movies actually displayed and their positions.
4. An organic action has no recommendation identifiers. A recommendation-driven action references
   the exact request and impression. A bandit action also requires the policy's real propensity.
5. Feedback applies once to its action within the configured attribution window. Corrections/undo
   remain append-only and deactivate the earlier signal instead of deleting the audit event.

The versioned Pydantic contracts are in `backend/contracts.py`. Producers reject unknown fields,
duplicate candidates/positions, untraceable attributed actions, invalid propensities, and
corrections that precede the original action.

## Feature contract

`recommender/features.py` is the single deterministic implementation used by offline and serving
adapters. Version `taste-v1` deduplicates by action ID, excludes undone signals, keeps watched and
skip neutral, treats wishlist as intent rather than enjoyment, bounds confidence by evidence, and
normalizes genre affinity. Confidence is evidence strength, not a probability of liking.

Any incompatible semantic change creates a new feature version. Golden tests compare offline and
serving output byte-for-byte for the same ordered or shuffled source events. Periodic full profile
reconstruction must equal incremental state before promotion.

## Initial attribution and reward policy

- Immediate swipe attribution window: 24 hours after an impression.
- Wishlist-to-watch attribution window: 30 days, reported separately from swipe reward.
- Dislike/not interested: reward -1.0; like: +1.0; love: +1.5; wishlist: +0.35.
- Watched and skipped: 0.0 until explicit satisfaction feedback exists.
- Vowpal Wabbit minimizes cost, so a valid attributed reward becomes `cost = -reward`.

Mappings and windows are configuration, are versioned with the policy, and are not retroactively
applied to old logs. Organic feedback cannot train or evaluate a contextual-bandit policy.

## Objectives and promotion criteria

Personal offline metrics: Recall, Precision, and NDCG at 10; catalog coverage; intra-list
diversity; novelty; eligible-candidate coverage; latency; and fallback rate. Online metrics:
positive-feedback and correction rates, cumulative attributed reward, availability accuracy, and
long-term catalog exposure.

Group metrics: predicted and observed mean/minimum satisfaction, score range/disagreement,
per-member candidate representation and final exposure, agreement/acceptance, time to decision,
veto rate, and post-watch satisfaction. Highly active members retain one defined vote/influence.

No advanced model is promoted merely because it is more complex. It must improve a declared
primary metric or document an accepted trade-off against popularity/content/hybrid baselines while
respecting latency, fairness, safety, coverage, and fallback guardrails. Bandit policies begin in
shadow mode and off-policy metrics are reported only for events with valid eligible sets and
propensities.
