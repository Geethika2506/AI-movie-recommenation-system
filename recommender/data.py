import hashlib
import json
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

DATASETS = {
    "latest-small": "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip",
    "32m": "https://files.grouplens.org/datasets/movielens/ml-32m.zip",
}


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    users: int
    movies: int
    ratings: int
    min_timestamp: int
    max_timestamp: int
    fingerprint: str


def download_movielens(name: str, destination: Path) -> Path:
    if name not in DATASETS:
        raise ValueError(f"dataset must be one of {sorted(DATASETS)}")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{name}.zip"
    if not archive.exists():
        urllib.request.urlretrieve(DATASETS[name], archive)  # noqa: S310
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)
        root_names = {part.split("/")[0] for part in bundle.namelist() if "/" in part}
    return destination / next(iter(root_names))


def load_and_validate(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ratings = pd.read_csv(root / "ratings.csv").rename(
        columns={"userId": "user_id", "movieId": "movie_id"}
    )
    movies = pd.read_csv(root / "movies.csv").rename(columns={"movieId": "movie_id"})
    links = pd.read_csv(root / "links.csv").rename(
        columns={"movieId": "movie_id", "imdbId": "imdb_id", "tmdbId": "tmdb_id"}
    )
    _require(ratings, {"user_id", "movie_id", "rating", "timestamp"}, "ratings")
    _require(movies, {"movie_id", "title", "genres"}, "movies")
    _require(links, {"movie_id", "imdb_id", "tmdb_id"}, "links")
    if not ratings["rating"].between(0.5, 5.0).all():
        raise ValueError("ratings outside MovieLens 0.5..5.0 range")
    if ratings[["user_id", "movie_id", "timestamp"]].isna().any().any():
        raise ValueError("ratings contain null keys")
    if movies[["movie_id", "title", "genres"]].isna().any().any():
        raise ValueError("movies contain missing required metadata")
    if ratings["timestamp"].lt(0).any():
        raise ValueError("ratings contain invalid timestamps")
    duplicate = ratings.duplicated(["user_id", "movie_id"], keep=False)
    if duplicate.any():
        raise ValueError(f"ratings contain {int(duplicate.sum())} duplicate user/movie rows")
    if movies["movie_id"].duplicated().any():
        raise ValueError("movies contain duplicate movie IDs")
    unknown = set(ratings["movie_id"]) - set(movies["movie_id"])
    if unknown:
        raise ValueError(f"ratings reference {len(unknown)} unknown movies")
    return movies, ratings, links


def profile_dataset(name: str, movies: pd.DataFrame, ratings: pd.DataFrame) -> DatasetProfile:
    stable = ratings.sort_values(["user_id", "movie_id", "timestamp"]).to_csv(index=False)
    fingerprint = hashlib.sha256(stable.encode()).hexdigest()
    return DatasetProfile(
        name=name,
        users=int(ratings["user_id"].nunique()),
        movies=int(movies["movie_id"].nunique()),
        ratings=len(ratings),
        min_timestamp=int(ratings["timestamp"].min()),
        max_timestamp=int(ratings["timestamp"].max()),
        fingerprint=fingerprint,
    )


def prepare_dataset(name: str, source: Path, output: Path) -> DatasetProfile:
    movies, ratings, links = load_and_validate(source)
    output.mkdir(parents=True, exist_ok=True)
    user_ids = sorted(ratings["user_id"].unique())
    movie_ids = sorted(movies["movie_id"].unique())
    mappings = {
        "users": {str(value): index for index, value in enumerate(user_ids)},
        "movies": {str(value): index for index, value in enumerate(movie_ids)},
    }
    movies.to_csv(output / "movies.csv", index=False)
    ratings.to_csv(output / "ratings.csv", index=False)
    links.to_csv(output / "links.csv", index=False)
    (output / "mappings.json").write_text(json.dumps(mappings, sort_keys=True))
    profile = profile_dataset(name, movies, ratings)
    (output / "dataset.json").write_text(json.dumps(asdict(profile), indent=2))
    return profile


def synthetic_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    movies = pd.DataFrame(
        [
            (1, "Space One (2000)", "Sci-Fi|Adventure"),
            (2, "Space Two (2001)", "Sci-Fi|Drama"),
            (3, "Quiet Story (2002)", "Drama"),
            (4, "Big Laugh (2003)", "Comedy"),
            (5, "Final Quest (2004)", "Adventure|Fantasy"),
            (6, "Other Laugh (2005)", "Comedy"),
        ],
        columns=["movie_id", "title", "genres"],
    )
    rows = []
    timestamp = 1_600_000_000
    for user_id, values in enumerate(([5, 4, 2, 1], [4, 5, 2, 1], [1, 2, 5, 4]), start=1):
        for movie_id, rating in enumerate(values, start=1):
            rows.append((user_id, movie_id, float(rating), timestamp))
            timestamp += 1
    ratings = pd.DataFrame(rows, columns=["user_id", "movie_id", "rating", "timestamp"])
    return movies, ratings


def _require(frame: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")
