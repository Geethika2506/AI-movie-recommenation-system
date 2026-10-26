"""Integration tests for group recommendation ranking and APIs."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app import create_app
from backend.config import Settings
from backend.models import Movie, UserMovieState
from backend.personal import apply_onboarding_preferences
from backend.recommendations import group_recommendations

A = {"X-Cinematch-Session": "alice-group-rec-session"}
B = {"X-Cinematch-Session": "bob-group-rec-session-xx"}


def app_for(tmp_path: Path) -> object:
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'group-rec.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
        )
    )


def seed_movies(session: Session, *, include_action: bool = False) -> None:
    rows = [
        Movie(
            id=1,
            title="Pure Drama",
            year=2000,
            genres=["Drama"],
            popularity_score=6.0,
        ),
        Movie(
            id=2,
            title="Pure Comedy",
            year=2001,
            genres=["Comedy"],
            popularity_score=6.0,
        ),
        Movie(
            id=3,
            title="Drama Comedy Blend",
            year=2002,
            genres=["Drama", "Comedy"],
            popularity_score=5.0,
        ),
    ]
    if include_action:
        rows.append(
            Movie(
                id=4,
                title="Neutral Action",
                year=2003,
                genres=["Action"],
                popularity_score=5.0,
            )
        )
    session.add_all(rows)
    session.commit()


def like_movie(client: TestClient, profile_id: int, movie_id: int, headers: dict[str, str]) -> None:
    response = client.put(
        f"/api/profiles/{profile_id}/preferences/{movie_id}",
        headers=headers,
        json={"action": "liked", "request_id": f"like-{profile_id}-{movie_id}"},
    )
    assert response.status_code == 200


def profile_id(client: TestClient, name: str, headers: dict[str, str]) -> int:
    response = client.post("/api/profiles", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return int(response.json()["id"])


def seed_taste(
    session: Session, profile_id: int, preferred: list[str], disliked: list[str] | None = None
) -> None:
    apply_onboarding_preferences(session, profile_id, preferred, disliked or [])
    session.commit()


def test_group_ranks_genre_compromise_above_single_member_favorites(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as session:
            seed_movies(session)
            alice = profile_id(client, "Alice", A)
            bob = profile_id(client, "Bob", B)
        like_movie(client, alice, 1, A)
        like_movie(client, bob, 2, B)

        alice_solo = client.get(
            f"/api/profiles/{alice}/recommendations",
            headers=A,
            params={"limit": 3},
        ).json()
        bob_solo = client.get(
            f"/api/profiles/{bob}/recommendations",
            headers=B,
            params={"limit": 3},
        ).json()
        alice_ids = [row["movie"]["id"] for row in alice_solo]
        bob_ids = [row["movie"]["id"] for row in bob_solo]
        assert 1 not in alice_ids
        assert 2 not in bob_ids
        assert alice_ids.index(3) < alice_ids.index(2)
        assert bob_ids.index(3) < bob_ids.index(1)

        with client.app.state.session_factory() as session:
            ranked = group_recommendations(session, [alice, bob], limit=10, mean_weight=0.75)
        titles = [movie.title for movie, _score, _reason in ranked]
        assert titles[0] == "Drama Comedy Blend"
        assert titles.index("Drama Comedy Blend") < titles.index("Pure Drama")
        assert titles.index("Drama Comedy Blend") < titles.index("Pure Comedy")
        assert all(reason for _movie, _score, reason in ranked)


def test_group_strategy_everyone_matters_prefers_fairer_pick(tmp_path: Path) -> None:
    """When tastes conflict, min-weighted strategy should not pick a one-sided winner."""
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as session:
            seed_movies(session)
            alice = profile_id(client, "Alice", A)
            bob = profile_id(client, "Bob", B)
        like_movie(client, alice, 1, A)
        like_movie(client, bob, 2, B)

        with client.app.state.session_factory() as session:
            balanced = group_recommendations(session, [alice, bob], limit=3, mean_weight=0.75)
            fair = group_recommendations(session, [alice, bob], limit=3, mean_weight=0.25)

        assert balanced[0][0].id == 3
        assert fair[0][0].id == 3
        blend_balanced = next(score for movie, score, _ in balanced if movie.id == 3)
        blend_fair = next(score for movie, score, _ in fair if movie.id == 3)
        assert blend_fair >= blend_balanced - 1e-6
        drama_balanced = next(score for movie, score, _ in balanced if movie.id == 1)
        drama_fair = next(score for movie, score, _ in fair if movie.id == 1)
        assert drama_fair <= drama_balanced + 1e-6


def test_group_api_endpoint_and_legacy_profile_list(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as session:
            seed_movies(session)
            alice = profile_id(client, "Alice", A)
            bob = profile_id(client, "Bob", B)
        like_movie(client, alice, 1, A)
        like_movie(client, bob, 2, B)

        group = client.post(
            "/api/groups",
            json={"name": "Movie night", "profile_id": alice},
            headers=A,
        ).json()
        invite = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice},
            headers=A,
        ).json()
        client.post(
            f"/api/group-invites/{invite['token']}/join",
            json={"profile_id": bob},
            headers=B,
        )

        group_recs = client.post(
            f"/api/groups/{group['id']}/recommendations",
            json={"requester_profile_id": alice, "strategy": "balanced", "limit": 5},
            headers=A,
        )
        assert group_recs.status_code == 200
        payload = group_recs.json()
        assert payload[0]["movie"]["title"] == "Drama Comedy Blend"
        assert all("profile_id" not in row for row in payload)
        assert all(row.get("reason") for row in payload)

        carol = profile_id(client, "Carol", A)
        dave = profile_id(client, "Dave", A)
        like_movie(client, carol, 1, A)
        like_movie(client, dave, 2, A)
        legacy = client.post(
            "/api/recommendations/group",
            json={"profile_ids": [carol, dave], "limit": 5},
            headers=A,
        )
        assert legacy.status_code == 200
        legacy_ids = [row["movie"]["id"] for row in legacy.json()]
        assert legacy_ids[0] == 3


def test_group_recommendations_honor_genre_filter_and_watched_exclusion(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as session:
            seed_movies(session, include_action=True)
            alice = profile_id(client, "Alice", A)
            bob = profile_id(client, "Bob", B)
            seed_taste(session, alice, ["Drama", "Comedy"], [])
            seed_taste(session, bob, ["Drama", "Comedy"], [])
            session.add(
                UserMovieState(profile_id=alice, movie_id=3, watched=True, watchlist=False)
            )
            session.commit()

        group = client.post(
            "/api/groups",
            json={"name": "Filters", "profile_id": alice},
            headers=A,
        ).json()
        invite = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice},
            headers=A,
        ).json()
        client.post(
            f"/api/group-invites/{invite['token']}/join",
            json={"profile_id": bob},
            headers=B,
        )

        filtered = client.post(
            f"/api/groups/{group['id']}/recommendations",
            json={
                "requester_profile_id": bob,
                "strategy": "balanced",
                "genres": ["Action"],
                "limit": 10,
            },
            headers=B,
        )
        assert filtered.status_code == 200
        assert {row["movie"]["title"] for row in filtered.json()} == {"Neutral Action"}

        without_watched = client.post(
            f"/api/groups/{group['id']}/recommendations",
            json={"requester_profile_id": alice, "strategy": "balanced", "limit": 10},
            headers=A,
        ).json()
        assert all(row["movie"]["id"] != 3 for row in without_watched)

        with_watched = client.post(
            f"/api/groups/{group['id']}/recommendations",
            json={
                "requester_profile_id": alice,
                "strategy": "balanced",
                "include_watched": True,
                "limit": 10,
            },
            headers=A,
        ).json()
        assert any(row["movie"]["id"] == 3 for row in with_watched)


def test_active_session_group_recommendations_return_ranked_deck(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as session:
            seed_movies(session)
            alice = profile_id(client, "Alice", A)
            bob = profile_id(client, "Bob", B)
        like_movie(client, alice, 1, A)
        like_movie(client, bob, 2, B)

        group = client.post(
            "/api/groups",
            json={"name": "Session night", "profile_id": alice},
            headers=A,
        ).json()
        invite = client.post(
            f"/api/groups/{group['id']}/invites",
            json={"profile_id": alice},
            headers=A,
        ).json()
        client.post(
            f"/api/group-invites/{invite['token']}/join",
            json={"profile_id": bob},
            headers=B,
        )
        session_resp = client.post(
            f"/api/groups/{group['id']}/sessions",
            headers=A,
            json={
                "requester_profile_id": alice,
                "participant_profile_ids": [alice, bob],
                "consensus_policy": "majority",
                "ranking_algorithm": "fair_consensus",
            },
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        deck = client.get(
            f"/api/sessions/{session_id}/recommendations",
            headers=A,
            params={"profile_id": alice, "limit": 5},
        )
        assert deck.status_code == 200
        rows = deck.json()
        assert len(rows) >= 1
        assert rows[0]["movie"]["title"] == "Drama Comedy Blend"
        assert all(row.get("reason") for row in rows)
