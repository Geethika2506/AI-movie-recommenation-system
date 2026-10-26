# Data rights and ML eligibility

Reviewed 2026-10-26. This engineering record is not legal advice. A source is excluded from
training and persistent feature generation unless its applicable terms expressly permit the use
or the project has written permission. The enforceable field classification is
`configs/data_sources.json`; changing it requires a new manifest version and review.

## TMDB

Official references:

- [TMDB API FAQ and attribution](https://developer.themoviedb.org/docs/faq)
- [TMDB API getting started](https://developer.themoviedb.org/docs/getting-started)
- [TMDB image basics](https://developer.themoviedb.org/docs/image-basics)
- [Movie watch providers](https://developer.themoviedb.org/reference/movie-watch-providers)

TMDB permits its developer API for non-commercial applications with attribution. Commercial use
requires a commercial agreement. The application must use an approved TMDB logo and display:

> This product uses the TMDB API but is not endorsed or certified by TMDB.

TMDB's watch-provider response is powered by JustWatch and requires visible JustWatch attribution.
It reports country-specific availability but does not provide complete deep links. Availability
must carry retrieval time and country and be shown as unknown when absent or stale.

The reviewed public documentation does not expressly grant model-training or persistent embedding
rights. Consequently, TMDB overviews, keywords, cast, crew, posters, and provider availability are
**display-only** in CineMatch. They cannot enter training text, taste features, collaborative
signals, or persisted embeddings without separate written permission. Poster paths are used to
construct approved CDN display URLs; source images are not committed or packaged.

## MovieLens

The authoritative license for the default dataset is the
[ml-latest-small README](https://files.grouplens.org/datasets/movielens/ml-latest-small-README.html).
It permits research use with acknowledgement and permits transformed redistribution under the same
conditions. It prohibits commercial or revenue-bearing use without prior GroupLens permission and
forbids implying endorsement. `latest-small` changes over time and is unsuitable for shared
benchmark claims without pinning a fingerprint.

CineMatch may use MovieLens ratings, timestamps, titles, and genres for local development and
research. MovieLens user IDs remain in a separate namespace and are never treated as application
users. Any commercial deployment must either document GroupLens permission or exclude MovieLens
data and artifacts derived from it.

## Sentence Transformer

`sentence-transformers/all-MiniLM-L6-v2` publishes Apache-2.0 model files, but its model card lists
upstream training datasets with their own terms. Until commercial provenance is cleared, CineMatch
approves this model only for development/research. Its output is eligible only when the input text
is independently ML-eligible. It therefore must not embed TMDB display-only text.

## Application interactions

Authenticated users' actions are eligible for recommendation improvement under the product privacy
policy and consent controls. Email, password material, invitation tokens, and other authentication
data are prohibited features. Organic actions, recommendation-attributed actions, and group votes
remain distinct. Corrections and undo events suppress prior signals; deletion requests propagate
to user features, future training sets, and attributable artifacts.

Group-session votes are temporary group evidence. They do not update long-term personal profiles
unless the user performs an explicit personal action such as adding the movie to a wishlist.

## Derived artifacts and provenance

Every dataset and model artifact records source fingerprints, the data-policy fingerprint,
transformations, feature/embedding/model versions, creation time, and applicable deletion lineage.
Artifacts inherit the most restrictive terms of their inputs. Do not commit raw datasets, API
responses, embeddings, user data, or trained weights. Before promotion, an artifact must pass the
machine-enforced field policy and be reproducible from versioned inputs.

## Retention and review

- Recommendation request/candidate logs may be shorter-lived than action records; the deployed
  privacy policy sets concrete periods.
- Expired availability data remains stored only if permitted and is never represented as current.
- User deletion removes direct data immediately and queues derived-profile/artifact invalidation.
- Re-review source terms before release, commercialization, or adding fields/providers/models.
