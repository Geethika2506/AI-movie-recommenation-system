"""Licensed-source movie embedding generation with versioned persistence."""

import hashlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Movie, MovieEmbedding
from recommender.data_governance import DataPolicyManifest

EMBEDDING_DIMENSIONS = 384
EMBEDDING_FEATURE_VERSION = "movielens-title-genres-v1"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class TextEncoder(Protocol):
    model_version: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_version = model_name
        self._model = SentenceTransformer(model_name, device="cpu")

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        result = self._model.encode(
            list(texts),
            batch_size=64,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(result, dtype=np.float32)


class DeterministicTestEncoder:
    model_version = "deterministic-test-encoder-v1"

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        rows = []
        for text in texts:
            raw = np.frombuffer(
                hashlib.shake_256(text.encode()).digest(EMBEDDING_DIMENSIONS * 4),
                dtype=np.uint32,
            )
            values = raw.astype(np.float32) / float(np.iinfo(np.uint32).max) - 0.5
            norm = float(np.linalg.norm(values))
            rows.append(values / max(norm, 1e-12))
        return np.vstack(rows) if rows else np.empty((0, EMBEDDING_DIMENSIONS), np.float32)


def movie_embedding_text(movie: Movie) -> str:
    """Use only fields approved for MovieLens ML; TMDB display text is excluded."""
    genres = ", ".join(sorted(movie.genres or []))
    return f"Title: {movie.title}. Genres: {genres or 'unknown'}."


def generate_movie_embeddings(
    session: Session,
    encoder: TextEncoder,
    policy: DataPolicyManifest,
    *,
    batch_size: int = 128,
    force: bool = False,
) -> int:
    policy.require_ml_fields(
        "movielens_latest_small", {"movie_id", "title", "genres"}, "development"
    )
    policy.require_ml_fields("all_minilm_l6_v2", {"model_output"}, "development")
    movies = list(session.scalars(select(Movie).order_by(Movie.id)))
    written = 0
    source_fingerprint = hashlib.sha256(
        "\n".join(movie_embedding_text(movie) for movie in movies).encode()
    ).hexdigest()
    for start in range(0, len(movies), batch_size):
        batch = movies[start : start + batch_size]
        vectors = encoder.encode([movie_embedding_text(movie) for movie in batch])
        if vectors.shape != (len(batch), EMBEDDING_DIMENSIONS):
            raise ValueError(
                f"encoder returned {vectors.shape}; expected {(len(batch), EMBEDDING_DIMENSIONS)}"
            )
        for movie, vector in zip(batch, vectors, strict=True):
            existing = session.get(MovieEmbedding, movie.id)
            if (
                existing
                and not force
                and existing.embedding_model_version == encoder.model_version
                and existing.source_fingerprint == source_fingerprint
            ):
                continue
            values = (vector / max(np.linalg.norm(vector), 1e-12)).astype(float).tolist()
            if existing:
                existing.embedding = values
                existing.embedding_model_version = encoder.model_version
                existing.feature_version = EMBEDDING_FEATURE_VERSION
                existing.source_fingerprint = source_fingerprint
            else:
                session.add(
                    MovieEmbedding(
                        movie_id=movie.id,
                        embedding=values,
                        embedding_model_version=encoder.model_version,
                        feature_version=EMBEDDING_FEATURE_VERSION,
                        source_fingerprint=source_fingerprint,
                    )
                )
            written += 1
        session.commit()
    return written
