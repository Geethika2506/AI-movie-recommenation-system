# Data provenance, terms, and attribution

## MovieLens

CineMatch uses datasets published by the
[GroupLens Research group](https://grouplens.org/datasets/movielens/) at the University of
Minnesota.

- Default: [MovieLens latest-small](https://files.grouplens.org/datasets/movielens/ml-latest-small-README.html)
- Optional larger workload: [MovieLens 32M](https://grouplens.org/datasets/movielens/32m/)
- General data index: [MovieLens datasets](https://grouplens.org/datasets/movielens/)

The archive README delivered with each dataset is the authoritative source for its contents,
recommended citation, permitted use, and disclaimer. Review it before use because the dataset is
not covered by CineMatch's MIT license. In particular, do not imply GroupLens endorsement, remove
required attribution, redistribute the dataset, or use it commercially unless the applicable
terms permit that action or GroupLens grants permission.

A deployment or report using MovieLens should identify:

- the exact dataset and archive version/date;
- GroupLens Research as the provider;
- the dataset URL and accompanying README;
- any filtering, splitting, or transformation performed;
- the code/model version and random seed used for an experiment.

The `latest` datasets can change over time. Reproducible experiments should retain a checksum and
ingestion timestamp without committing the archive to this repository.

## TMDB enrichment

TMDB is optional and requires `CINEMATCH_TMDB_TOKEN`. MovieLens links can be used to associate
records with TMDB identifiers, after which approved API responses may enrich the UI with posters
or metadata.

Use is governed by the [TMDB API terms](https://www.themoviedb.org/documentation/api/terms-of-use)
and [TMDB developer documentation](https://developer.themoviedb.org/docs/getting-started).
Follow current attribution, image, caching, and branding requirements. Include the required notice:

> This product uses the TMDB API but is not endorsed or certified by TMDB.

Never commit a TMDB token. Core ingestion, training, evaluation, and recommendations must remain
usable without TMDB.

Run `uv run --python 3.11 python -m scripts.cli enrich` after preparation. The command reads
`links.csv`, requests movie details plus credits with timeouts/retries, caches responses, and
appends completed records to `data/processed/latest-small/tmdb.jsonl`. Existing movie IDs are
skipped on reruns, so interrupted enrichment resumes without re-fetching completed records.

## Generated files

SQLite databases, downloaded archives, processed tables, evaluation outputs, and trained weights
are generated data or artifacts. They are ignored by Git by default. Before sharing any generated
file, check whether it contains third-party data and whether the source terms permit distribution.

## Software dependencies

Python and npm dependencies retain their respective licenses. A release owner should produce and
review a dependency license inventory before distributing a packaged product.
