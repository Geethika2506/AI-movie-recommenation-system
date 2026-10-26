import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
import torch
import typer
from sqlalchemy import MetaData, Table, create_engine, select

from backend.config import get_settings
from backend.db import make_engine, make_session_factory
from backend.models import ModelPromotion, PreferenceEvent
from backend.posters import enrich_wikipedia_posters
from backend.tmdb import TMDBClient
from backend.tmdb_catalog import import_discover_movies
from recommender.artifacts import ArtifactManifest
from recommender.benchmark import BenchmarkConfig, run_benchmark
from recommender.collaborative import ImplicitALSRecommender
from recommender.data import download_movielens, prepare_dataset
from recommender.data_governance import DataPolicyManifest
from recommender.embeddings import SentenceTransformerEncoder, generate_movie_embeddings
from recommender.evaluation import chronological_split, evaluate_predictions
from recommender.mf import ExplicitMF, MFConfig

app = typer.Typer(help="Prepare data, train models, and evaluate CineMatch.")


@app.command("record-promotion")
def record_promotion(
    model_name: Annotated[str, typer.Option()],
    to_version: Annotated[str, typer.Option()],
    decision: Annotated[str, typer.Option(help="promote, reject, or rollback")],
    reason: Annotated[str, typer.Option()],
    from_version: Annotated[str | None, typer.Option()] = None,
    metrics_json: Annotated[str, typer.Option()] = "{}",
) -> None:
    """Persist an auditable model promotion, rejection, or rollback decision."""
    if decision not in {"promote", "reject", "rollback"}:
        raise typer.BadParameter("decision must be promote, reject, or rollback")
    metrics = json.loads(metrics_json)
    if not isinstance(metrics, dict):
        raise typer.BadParameter("metrics-json must contain an object")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        session.add(
            ModelPromotion(
                model_name=model_name,
                from_version=from_version,
                to_version=to_version,
                decision=decision,
                reason=reason,
                metrics=metrics,
            )
        )
        session.commit()
    engine.dispose()
    typer.echo(f"Recorded {decision} decision for {model_name}:{to_version}")


@app.command("train-collaborative")
def train_collaborative(
    artifact_dir: Annotated[Path, typer.Option()] = Path("artifacts/implicit-als-v1"),
    factors: Annotated[int, typer.Option(min=2, max=256)] = 32,
    iterations: Annotated[int, typer.Option(min=1, max=200)] = 20,
) -> None:
    """Train confidence-weighted ALS from corrected application interactions."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        events = list(
            session.execute(
                select(
                    PreferenceEvent.profile_id,
                    PreferenceEvent.movie_id,
                    PreferenceEvent.action,
                ).where(PreferenceEvent.undone.is_(False))
            )
        )
    engine.dispose()
    frame = pd.DataFrame(events, columns=["user_id", "movie_id", "action"])
    frame["user_namespace"] = "app"
    fingerprint = (
        __import__("hashlib")
        .sha256(frame.sort_values(["user_id", "movie_id", "action"]).to_csv(index=False).encode())
        .hexdigest()
    )
    model = ImplicitALSRecommender(factors=factors, iterations=iterations).fit(frame)
    artifact = model.save(artifact_dir, fingerprint)
    typer.echo(f"Wrote collaborative model to {artifact.model_path}")


@app.command()
def embed(
    model: Annotated[str, typer.Option()] = "sentence-transformers/all-MiniLM-L6-v2",
    batch_size: Annotated[int, typer.Option(min=1, max=1024)] = 128,
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Generate licensed-input movie embeddings on CPU and upsert them."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    policy = DataPolicyManifest.load(Path("configs/data_sources.json"))
    with factory() as session:
        written = generate_movie_embeddings(
            session,
            SentenceTransformerEncoder(model),
            policy,
            batch_size=batch_size,
            force=force,
        )
    engine.dispose()
    typer.echo(f"Wrote {written} movie embeddings")


@app.command("import-legacy-sqlite")
def import_legacy_sqlite(
    source: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Idempotently copy legacy SQLite rows into the configured migrated database."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        raise typer.BadParameter("target CINEMATCH_DATABASE_URL must not be SQLite")
    source_engine = create_engine(f"sqlite:///{source}")
    target_engine = make_engine(settings.database_url)
    source_metadata = MetaData()
    target_metadata = MetaData()
    source_metadata.reflect(source_engine)
    target_metadata.reflect(target_engine)
    order = [
        "movies",
        "profiles",
        "ratings",
        "interactions",
        "user_movie_states",
        "preference_events",
        "groups",
        "group_members",
        "group_invites",
        "model_runs",
    ]
    copied: dict[str, int] = {}
    with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
        for table_name in order:
            if table_name not in source_metadata.tables or table_name not in target_metadata.tables:
                continue
            source_table = Table(table_name, source_metadata, autoload_with=source_engine)
            target_table = Table(table_name, target_metadata, autoload_with=target_engine)
            columns = [
                column.name for column in source_table.columns if column.name in target_table.c
            ]
            rows = [
                dict(row)
                for row in source_connection.execute(
                    select(*[source_table.c[name] for name in columns])
                ).mappings()
            ]
            primary_key = next(iter(target_table.primary_key.columns), None)
            if primary_key is not None and rows:
                existing = set(target_connection.execute(select(primary_key)).scalars())
                rows = [row for row in rows if row.get(primary_key.name) not in existing]
            if rows:
                target_connection.execute(target_table.insert(), rows)
            copied[table_name] = len(rows)
    source_engine.dispose()
    target_engine.dispose()
    typer.echo(json.dumps(copied, indent=2, sort_keys=True))


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


@app.command("import-tmdb-discover")
def import_tmdb_discover_cmd(
    years: Annotated[int, typer.Option(min=1, max=10, help="Import titles from the last N years")] = 3,
    limit: Annotated[int, typer.Option(min=1, max=2000, help="Maximum TMDB titles to upsert")] = 500,
) -> None:
    """Import popular recent movies from TMDB discover into the application catalog."""
    settings = get_settings()
    client = TMDBClient(settings.tmdb_token, settings.data_dir / "tmdb-cache")
    if not client.enabled:
        typer.echo("TMDB import skipped: CINEMATCH_TMDB_TOKEN is not set.", err=True)
        raise typer.Exit(code=1)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        inserted, updated = import_discover_movies(
            session, client, years=years, limit=limit
        )
    engine.dispose()
    typer.echo(f"TMDB discover import complete: inserted={inserted}, updated={updated}")
    typer.echo("Next: uv run python -m scripts.cli embed --force")


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
                provider_results = (payload.get("watch/providers") or {}).get("results") or {}
                providers = [
                    {
                        "country": country,
                        "provider_id": provider["provider_id"],
                        "provider_name": provider["provider_name"],
                        "access_type": access_type,
                        "source_url": country_data.get("link"),
                    }
                    for country, country_data in provider_results.items()
                    for access_type in ("flatrate", "free", "ads", "rent", "buy")
                    for provider in country_data.get(access_type, [])
                    if provider.get("provider_id") and provider.get("provider_name")
                ]
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
                    "vote_average": payload.get("vote_average"),
                    "keywords": [
                        item["name"]
                        for item in (payload.get("keywords") or {}).get("keywords", [])
                        if item.get("name")
                    ],
                    "providers": providers,
                    "fetched_at": datetime.now(UTC).isoformat(),
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
    posters = enrich_wikipedia_posters(make_session_factory(engine), settings.data_dir, limit=limit)
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
