"""Genre helpers that work across SQLite (dev/tests) and PostgreSQL (Compose)."""

from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement

from backend.models import Movie


def movie_has_genre(genre: str, *, dialect: str) -> ColumnElement[bool]:
    """Return a SQLAlchemy filter for movies whose JSON genre list includes ``genre``."""
    if dialect == "postgresql":
        return cast(Movie.genres, JSONB).contains([genre])
    return Movie.genres.contains([genre])
