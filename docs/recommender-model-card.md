# CineMatch recommender model card

## Summary

CineMatch is a learning and portfolio recommendation system for ranking movies from explicit
ratings. It includes four strategies:

- **Popularity:** a non-personalized baseline based on aggregate rating evidence.
- **Content:** item-to-item similarity from genres and available metadata.
- **Explicit matrix factorization:** PyTorch user/item latent factors trained to estimate ratings.
- **Hybrid:** a configurable blend of available baseline, content, and collaborative signals.

It is not designed for consequential decisions, child-safety decisions, psychological profiling,
or inferring sensitive attributes.

## Intended use

- Local exploration of movie recommendations.
- Demonstrating an end-to-end ML product and baseline-to-hybrid progression.
- Reproducible offline comparison on MovieLens.
- Small read-only portfolio demonstrations.

It should not be presented as production-ready without load, privacy, abuse, security, and online
quality evaluation.

## Inputs and outputs

Inputs are MovieLens movie metadata and explicit user ratings. Optional TMDB data affects
presentation or metadata, not the availability of the core system. Outputs are ranked movie IDs
with display metadata, component scores when available, and concise recommendation reasons.

A predicted score is a ranking signal, not a probability or guarantee that a person will enjoy a
movie.

## Cold start

- **Unknown user:** return popularity items and invite the person to rate a varied starter set.
- **Few ratings:** blend popularity/content more heavily; avoid overstating personalization.
- **Unknown item:** content can score an item if metadata exists; collaborative scoring waits for
  interactions or retraining.
- **Missing artifacts:** fall back to the strongest available baseline and report model status
  honestly.

The UI should distinguish “popular now in this dataset” from “personalized for you.”

## Evaluation

The measured 2026-10-19 latest-small run used seed 42, chronological validation/test holdouts,
full-catalogue ranking, and rating >= 4 as positive. Popularity achieved the best test NDCG@10
(0.0276), followed by matrix factorization (0.0234), hybrid (0.0166), and content (0.0067).
MF rating prediction measured RMSE 0.9884 and MAE 0.7643. The hybrid's weights were selected only
on validation, but it did not beat popularity; popularity is consequently the active default.
See the generated `artifacts/evaluation/report.md` after running evaluation for coverage,
diversity, popularity bias, counts, and latency details.

Evaluation must keep a user's held-out interactions out of training and feature construction.
Record dataset checksum/version, split method, minimum activity filters, seed, hyperparameters,
hardware, package lock, and uncertainty across repeated runs where practical. A lower RMSE does not
necessarily imply a more useful top-K experience, so rating and ranking metrics should be reviewed
together.

## Limitations and risks

- MovieLens users are self-selected and are not representative of all audiences.
- Historical interactions encode exposure, selection, popularity, and demographic biases.
- Genres and sparse metadata oversimplify why people like movies.
- Matrix factorization favors users/items with interaction history and cannot explain causality.
- Offline metrics cannot measure novelty fatigue, trust, satisfaction, or changing taste.
- Popularity fallback can amplify already-popular titles and reduce catalog diversity.
- Explicit feedback may be sparse, inconsistent, strategic, or outdated.
- Latest-small is useful for development but too small for broad product conclusions.
- TMDB metadata can be missing, stale, localized differently, or governed by changing terms.
- Explanations based on component scores are descriptive, not causal explanations.

## Operational considerations

Model metadata should include the training data identity and timestamp. Replace artifacts
atomically, validate compatibility before loading, and retain a safe fallback. Monitor score
distributions, fallback rate, catalog coverage, missing metadata, and latency if the system is
hosted. Retraining frequency should be based on measured drift, not an assumed schedule.
