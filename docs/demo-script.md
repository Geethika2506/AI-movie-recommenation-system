# Three-minute demo script

## 0:00–0:25 — Frame the problem

“CineMatch is a local-first movie recommender that handles the journey from no preference data to
personalized and group discovery. It uses FastAPI, React/Vite, SQLite, and multiple recommendation
strategies. The default GroupLens MovieLens latest-small dataset keeps the demo reproducible; TMDB
is optional.”

Show the home/discovery screen and briefly identify whether the current session is live or using
labeled demo fallback data.

## 0:25–0:55 — Show cold start

Create or select a new profile.

“A new profile has no collaborative history, so the application does not pretend to know this
person. It starts from popularity and content signals and asks for explicit ratings.”

Browse/search for a movie and open its details or similar-title view.

## 0:55–1:30 — Add preference signal

Rate several deliberately varied movies.

“Ratings are explicit feedback stored locally. Content features can respond immediately, while
matrix-factorization recommendations depend on a compatible trained artifact. Already-rated items
are filtered from normal recommendation results.”

Refresh recommendations and point to one concise reason. Describe it as the model's contributing
signal, not a causal explanation.

## 1:30–2:00 — Explain the hybrid

Show recommendations and, if implemented in the current build, change discovery or create a group.

“The hybrid combines normalized signals from popularity, metadata similarity, and PyTorch explicit
matrix factorization. If a signal is unavailable, it degrades to a valid baseline. Group results
aggregate distinct profiles rather than inventing a shared user.”

Do not quote quality or speed metrics unless they are visible in generated evaluation output.

## 2:00–2:30 — Show engineering evidence

Open FastAPI `/docs`, then the evaluation/model-status view.

“The UI and model package are separated by a documented API. Model metadata is intended to track
the dataset fingerprint, parameters, version, and measured metrics. Current pending fields are
shown as pending—not replaced with sample claims.”

Mention the named Docker volumes for SQLite/data and artifacts.

## 2:30–3:00 — Close with trade-offs

“CineMatch is intentionally local-first. SQLite is ideal for this portfolio workload but not a
multi-writer production database. MovieLens and optional TMDB content retain their own terms.
Public free hosts often have ephemeral filesystems, so the safe portfolio deployment is read-only
or static unless persistent storage, authentication, and licensing are addressed.”

End on the architecture or recommendation screen:

“The key result is the full decision path: honest cold start, progressively richer personalization,
explicit fallback behavior, and reproducible evaluation. Benchmark values remain pending until the
evaluation suite is run.”

## Before recording

- Start from a clean, known local profile and verify the API is healthy.
- Confirm whether TMDB is enabled and avoid showing tokens.
- Confirm the dataset/model version shown by the app.
- Clearly label fallback data or read-only mode.
- Keep a local recording path if network-dependent enrichment fails.
- Never improvise metrics that are not in generated, versioned output.
