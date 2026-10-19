import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import torch
import typer

from backend.config import get_settings
from backend.db import make_engine, make_session_factory
from backend.posters import enrich_wikipedia_posters
from backend.tmdb import TMDBClient
from recommender.artifacts import ArtifactManifest
from recommender.benchmark import BenchmarkConfig, run_benchmark
from recommender.data import download_movielens, prepare_dataset
from recommender.evaluation import chronological_split, evaluate_predictions
from recommender.mf import ExplicitMF, MFConfig

app = typer.Typer(help="Prepare data, train models, and evaluate CineMatch.")


@app.command()
def prepare(
    dataset: Annotated[str, typer.Option(help="latest-small or 32m")] = "latest-small",
    data_dir: Annotated[Path, typer.Option()] = Path("data"),
    source: Annotated[
        Path | None, typer.Option(help="Existing extracted MovieLens directory")
    ] = None,
) -> None:
    """Download (if necessary), validate, map and fingerprint MovieLens data."""
    raw = source or download_movielens(dataset, data_dir / "raw")
    profile = prepare_dataset(dataset, raw, data_dir / "processed" / dataset)
    typer.echo(json.dumps(asdict(profile), indent=2))


@app.command()
def enrich(
    data_dir: Annotated[Path, typer.Option()] = Path("data/processed/latest-small"),
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Resumably enrich prepared movies with optional TMDB metadata and posters."""
    settings = get_settings()
    client = TMDBClient(settings.tmdb_token, settings.data_dir / "tmdb-cache")
    if client.enabled:
        links = pd.read_csv(data_dir / "links.csv")
        output = data_dir / "tmdb.jsonl"
        completed: set[int] = set()
        if output.exists():
            completed = {
                int(json.loads(line)["movie_id"])
                for line in output.read_text().splitlines()
                if line.strip()
            }
        written = 0
        with output.open("a") as stream:
            for row in links.dropna(subset=["tmdb_id"]).itertuples(index=False):
                movie_id = int(row.movie_id)
                if movie_id in completed:
                    continue
                payload = client.movie(int(row.tmdb_id))
                if payload is None:
                    continue
                credits = payload.get("credits") or {}
                crew = credits.get("crew") or []
                record = {
                    "movie_id": movie_id,
                    "tmdb_id": int(row.tmdb_id),
                    "poster_path": payload.get("poster_path"),
                    "overview": payload.get("overview") or None,
                    "runtime": payload.get("runtime"),
                    "language": payload.get("original_language"),
                    "cast": [
                        member["name"]
                        for member in (credits.get("cast") or [])[:5]
                        if member.get("name")
                    ],
                    "director": next(
                        (
                            member["name"]
                            for member in crew
                            if member.get("job") == "Director" and member.get("name")
                        ),
                        None,
                    ),
                }
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                written += 1
                if limit is not None and written >= limit:
                    break
        typer.echo(f"Wrote {written} TMDB records to {output}")
    else:
        typer.echo("TMDB enrichment skipped: CINEMATCH_TMDB_TOKEN is not set.")

    engine = make_engine(settings.database_url)
    posters = enrich_wikipedia_posters(
        make_session_factory(engine), settings.data_dir, limit=limit
    )
    engine.dispose()
    typer.echo(f"Wrote {posters} Wikipedia poster records")


@app.command()
def train(
    data_dir: Annotated[Path, typer.Option()] = Path("data/processed/latest-small"),
    artifact_dir: Annotated[Path, typer.Option()] = Path("artifacts/mf-v1"),
    factors: Annotated[int, typer.Option(min=2, max=256)] = 32,
    epochs: Annotated[int, typer.Option(min=1, max=500)] = 30,
) -> None:
    """Train explicit matrix factorization and emit a self-describing artifact."""
    ratings = pd.read_csv(data_dir / "ratings.csv")
    metadata = json.loads((data_dir / "dataset.json").read_text())
    mappings = json.loads((data_dir / "mappings.json").read_text())
    user_map = {int(key): value for key, value in mappings["users"].items()}
    movie_map = {int(key): value for key, value in mappings["movies"].items()}
    split = chronological_split(ratings)
    users = split.train["user_id"].map(user_map).to_numpy()
    items = split.train["movie_id"].map(movie_map).to_numpy()
    values = split.train["rating"].to_numpy(dtype=np.float32)
    config = MFConfig(factors=factors, epochs=epochs)
    model = ExplicitMF.train_model(
        users,
        items,
        values,
        len(user_map),
        len(movie_map),
        config=config,
        validation=(
            split.validation["user_id"].map(user_map).to_numpy(),
            split.validation["movie_id"].map(movie_map).to_numpy(),
            split.validation["rating"].to_numpy(dtype=np.float32),
        ),
    )
    model.save(
        artifact_dir / "model.pt",
        {
            "mappings": "mappings.json",
            "config": asdict(config),
            "seed": config.seed,
            "dataset_fingerprint": metadata["fingerprint"],
            "training": model.training_summary_,
        },
    )
    (artifact_dir / "mappings.json").parent.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "mappings.json").write_text(json.dumps(mappings))
    predicted = (
        model(
            pd_to_tensor(split.test["user_id"].map(user_map)),
            pd_to_tensor(split.test["movie_id"].map(movie_map)),
        )
        .detach()
        .numpy()
    )
    metrics = evaluate_predictions(split.test["rating"].to_numpy(), predicted)
    manifest = ArtifactManifest.create(
        "explicit-mf",
        "mf-v1",
        metadata["fingerprint"],
        asdict(config),
        metrics,
        {"model": "model.pt", "mappings": "mappings.json"},
    )
    typer.echo(str(manifest.write(artifact_dir)))


@app.command()
def evaluate(
    data_dir: Annotated[Path, typer.Option()] = Path("data/processed/latest-small"),
    output_dir: Annotated[Path, typer.Option()] = Path("artifacts/evaluation"),
    k: Annotated[int, typer.Option(min=1, max=100)] = 10,
    seed: Annotated[int, typer.Option(min=0)] = 42,
    factors: Annotated[int, typer.Option(min=2, max=256)] = 32,
    epochs: Annotated[int, typer.Option(min=1, max=500)] = 30,
    batch_size: Annotated[int, typer.Option(min=1)] = 1024,
    patience: Annotated[int, typer.Option(min=1)] = 4,
) -> None:
    """Compare all recommenders with one leakage-safe full-catalogue protocol."""
    results = run_benchmark(
        data_dir,
        output_dir,
        BenchmarkConfig(
            k=k,
            seed=seed,
            factors=factors,
            epochs=epochs,
            batch_size=batch_size,
            patience=patience,
        ),
    )
    typer.echo(
        json.dumps(
            {
                "results": str(output_dir / "results.json"),
                "report": str(output_dir / "report.md"),
                "chart": str(output_dir / "metrics.svg"),
                "test_metrics": results["test_metrics"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def pd_to_tensor(series: pd.Series) -> torch.Tensor:
    return torch.as_tensor(series.to_numpy(), dtype=torch.long)


if __name__ == "__main__":
    app()
