from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.contracts import (
    ActionEvent,
    CandidateAction,
    CandidateSetEvent,
    DisplayedItem,
    ImpressionEvent,
    InteractionOrigin,
)
from recommender.content import ContentRecommender
from recommender.data import synthetic_data
from recommender.data_governance import DataPolicyManifest
from recommender.features import TasteSignal, offline_taste_features, serving_taste_features

ROOT = Path(__file__).parents[1]


def test_data_policy_blocks_display_only_and_unclassified_fields() -> None:
    manifest = DataPolicyManifest.load(ROOT / "configs/data_sources.json")
    manifest.require_ml_fields(
        "movielens_latest_small",
        {"movie_id", "title", "genres", "rating"},
        "development",
    )
    with pytest.raises(PermissionError, match="not ML-eligible"):
        manifest.require_ml_fields("tmdb_api", {"overview"}, "development")
    with pytest.raises(ValueError, match="unclassified"):
        manifest.require_ml_fields("tmdb_api", {"unreviewed_field"}, "development")
    with pytest.raises(PermissionError, match="not approved"):
        manifest.require_ml_fields("movielens_latest_small", {"rating"}, "commercial")
    assert len(manifest.fingerprint()) == 64


def test_content_builder_enforces_source_field_policy() -> None:
    movies, _ = synthetic_data()
    with pytest.raises(PermissionError, match="not ML-eligible"):
        ContentRecommender().fit(movies, source="tmdb_api")


def test_candidate_and_impression_contracts_reject_ambiguous_logs() -> None:
    request_id = uuid4()
    with pytest.raises(ValidationError, match="candidate movie IDs must be unique"):
        CandidateSetEvent(
            request_id=request_id,
            candidates=[
                CandidateAction(movie_id=1, source="content", score=0.5),
                CandidateAction(movie_id=1, source="popular", score=0.4),
            ],
        )
    with pytest.raises(ValidationError, match="positions must be unique"):
        ImpressionEvent(
            impression_id=uuid4(),
            request_id=request_id,
            displayed=[
                DisplayedItem(movie_id=1, position=0),
                DisplayedItem(movie_id=2, position=0),
            ],
        )
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        DisplayedItem(movie_id=1, position=0, selection_probability=1.1)


def test_organic_and_recommendation_actions_are_distinct_and_traceable() -> None:
    common = {
        "action_id": uuid4(),
        "user_id": 1,
        "movie_id": 2,
        "action": "liked",
    }
    organic = ActionEvent(**common, origin=InteractionOrigin.ORGANIC)
    assert organic.request_id is None
    with pytest.raises(ValidationError, match="require request_id"):
        ActionEvent(**common, origin=InteractionOrigin.RECOMMENDATION)
    with pytest.raises(ValidationError, match="cannot claim"):
        ActionEvent(
            **common,
            origin=InteractionOrigin.ORGANIC,
            request_id=uuid4(),
            impression_id=uuid4(),
        )
    with pytest.raises(ValidationError, match="cannot precede"):
        ActionEvent(
            **common,
            origin=InteractionOrigin.ORGANIC,
            occurred_at=datetime.now(UTC),
            undone_at=datetime.now(UTC) - timedelta(days=1),
        )


def test_offline_and_serving_features_match_and_ignore_undo_duplicates() -> None:
    signals = [
        TasteSignal("a-1", 1, "liked", ("Sci-Fi", "Adventure")),
        TasteSignal("a-2", 2, "disliked", ("Comedy",)),
        TasteSignal("a-3", 3, "watched", ("Drama",)),
        TasteSignal("a-4", 4, "love", ("Sci-Fi",)),
        TasteSignal("a-5", 5, "watchlist", ("Drama",), undone=True),
        TasteSignal("a-1", 1, "liked", ("Sci-Fi", "Adventure")),
    ]
    offline = offline_taste_features(signals)
    serving = serving_taste_features(reversed(signals))
    assert offline == serving
    assert offline.event_count == 4
    assert offline.positive_count == 2
    assert offline.negative_count == 1
    assert 0 < offline.confidence < 1
    assert dict(offline.genre_affinity)["Sci-Fi"] > 0


def test_conflicting_duplicate_action_is_rejected() -> None:
    signals = [
        TasteSignal("same", 1, "liked", ("Drama",)),
        TasteSignal("same", 1, "disliked", ("Drama",)),
    ]
    with pytest.raises(ValueError, match="conflicting duplicate"):
        serving_taste_features(signals)
