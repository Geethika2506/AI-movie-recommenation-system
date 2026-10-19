from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.models import GroupInvite, Movie, PreferenceEvent, UserMovieState
from backend.recommendations import aggregate_member_scores

A = {"X-Cinematch-Session": "alice-owned-session-key"}
B = {"X-Cinematch-Session": "bob-owned-session-key-xx"}


def app_for(tmp_path: Path) -> object:
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
        )
    )


def seed(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        session.add_all(
            [
                Movie(id=1, title="Space Like", year=2000, genres=["Sci-Fi"]),
                Movie(id=2, title="Space Next", year=2001, genres=["Sci-Fi"]),
                Movie(id=3, title="Comedy", year=2002, genres=["Comedy"]),
                Movie(id=4, title="Drama", year=2003, genres=["Drama"]),
            ]
        )
        session.commit()


def profile(client: TestClient, name: str, headers: dict[str, str]) -> int:
    response = client.post("/api/profiles", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return int(response.json()["id"])


def test_swipe_states_idempotency_ownership_undo_and_exclusion(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        seed(client)
        alice = profile(client, "Alice", A)
        url = f"/api/profiles/{alice}/preferences/1"
        payload = {"action": "liked", "request_id": "like-request-0001"}

        first = client.put(url, json=payload, headers=A)
        duplicate = client.put(url, json=payload, headers=A)
        assert first.status_code == 200
        assert duplicate.json()["action_id"] == first.json()["action_id"]
        assert duplicate.json()["recommendation_version"] == 1
        assert first.json()["watched"] is True
        assert first.json()["preference"] == "liked"

        assert client.put(
            f"/api/profiles/{alice}/preferences/2",
            json={"action": "disliked", "request_id": "dislike-request-2"},
            headers=B,
        ).status_code == 403
        assert client.put(
            f"/api/profiles/{alice}/preferences/2",
            json={"action": "skip", "request_id": "skip-request-0002"},
            headers=A,
        ).json()["watched"] is False
        with client.app.state.session_factory() as session:
            assert session.scalar(
                select(UserMovieState).where(
                    UserMovieState.profile_id == alice,
                    UserMovieState.movie_id == 2,
                )
            ) is None

        undo_skip = client.post(
            f"/api/profiles/{alice}/preferences/2/undo",
            json={"action_id": 2},
            headers=A,
        )
        assert undo_skip.status_code == 200
        undo_like = client.post(
            f"/api/profiles/{alice}/preferences/1/undo",
            json={"action_id": first.json()["action_id"]},
            headers=A,
        )
        assert undo_like.status_code == 200
        assert undo_like.json()["preference"] is None

        disliked = client.put(
            f"/api/profiles/{alice}/preferences/1",
            json={"action": "disliked", "request_id": "dislike-request-3"},
            headers=A,
        )
        watchlist = client.put(
            f"/api/profiles/{alice}/preferences/3",
            json={"action": "watchlist", "request_id": "watchlist-request"},
            headers=A,
        )
        assert disliked.json()["preference"] == "disliked"
        assert watchlist.json()["watchlist"] is True
        assert watchlist.json()["watched"] is False
        recommended_ids = {
            row["movie"]["id"]
            for row in client.get(
                f"/api/profiles/{alice}/recommendations",
                headers=A,
            ).json()
        }
        assert 1 not in recommended_ids
        assert 3 in recommended_ids


def test_recommendations_follow_likes_and_discovery(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        seed(client)
        alice = profile(client, "Alice", A)
        liked = client.put(
            f"/api/profiles/{alice}/preferences/1",
            json={"action": "liked", "request_id": "like-scifi"},
            headers=A,
        )
        assert liked.status_code == 200
        familiar = client.get(
            f"/api/profiles/{alice}/recommendations",
            params={"limit": 10, "discovery": 0},
            headers=A,
        ).json()
        familiar_ids = [row["movie"]["id"] for row in familiar]
        assert familiar_ids.index(2) < familiar_ids.index(3)
        adventurous = client.get(
            f"/api/profiles/{alice}/recommendations",
            params={"limit": 10, "discovery": 100},
            headers=A,
        ).json()
        assert {row["movie"]["id"] for row in adventurous} == {2, 3, 4}
        assert len(adventurous) == 3


def test_invite_lifecycle_membership_privacy_and_cache_versions(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        seed(client)
        alice = profile(client, "Alice", A)
        bob = profile(client, "Bob", B)
        group = client.post(
            "/api/groups",
            json={"name": "Friday", "profile_id": alice},
            headers=A,
        ).json()
        invite = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice, "expires_in_hours": 2},
            headers=A,
        ).json()
        joined = client.post(
            f"/api/group-invites/{invite['token']}/join",
            json={"profile_id": bob},
            headers=B,
        )
        assert joined.status_code == 200
        assert len(joined.json()["members"]) == 2
        assert "preference" not in joined.text

        duplicate = client.post(
            f"/api/group-invites/{invite['token']}/join",
            json={"profile_id": bob},
            headers=B,
        )
        assert len(duplicate.json()["members"]) == 2
        before_version = duplicate.json()["recommendation_version"]
        client.put(
            f"/api/profiles/{bob}/preferences/3",
            json={"action": "liked", "request_id": "bob-like-comedy"},
            headers=B,
        )
        groups = client.get("/api/groups", headers=B).json()
        assert groups[0]["recommendation_version"] == before_version + 1

        recommendations = client.post(
            f"/api/groups/{group['id']}/recommendations",
            json={"requester_profile_id": bob, "strategy": "everyone_matters"},
            headers=B,
        )
        assert recommendations.status_code == 200
        assert all("profile_id" not in row for row in recommendations.json())

        revoked = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice},
            headers=A,
        ).json()
        assert client.delete(
            f"/api/groups/{group['id']}/invites/{revoked['id']}?profile_id={alice}",
            headers=A,
        ).status_code == 204
        assert client.post(
            f"/api/group-invites/{revoked['token']}/join",
            json={"profile_id": bob},
            headers=B,
        ).status_code == 410

        expired = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice},
            headers=A,
        ).json()
        with client.app.state.session_factory() as session:
            record = session.scalar(
                select(GroupInvite).where(
                    GroupInvite.token_hash.is_not(None),
                    GroupInvite.id == expired["id"],
                )
            )
            assert record is not None
            record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            session.commit()
        assert client.post(
            f"/api/group-invites/{expired['token']}/join",
            json={"profile_id": bob},
            headers=B,
        ).status_code == 410

        with client.app.state.session_factory() as session:
            assert session.scalar(select(PreferenceEvent).where(PreferenceEvent.profile_id == bob))


def test_group_score_formula_is_hand_checkable() -> None:
    scores = [0.8, 0.4]
    assert aggregate_member_scores(scores, mean_weight=1.0) == pytest.approx(0.6)
    assert aggregate_member_scores(scores, mean_weight=0.0) == pytest.approx(0.4)
    assert aggregate_member_scores(scores, mean_weight=0.75) == pytest.approx(0.55)
    assert aggregate_member_scores(scores, mean_weight=0.25) == pytest.approx(0.45)
