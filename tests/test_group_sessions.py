from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.group_sessions import aggregate_group_scores
from backend.models import Movie

A = {"X-Cinematch-Session": "alice-owned-session-key"}
B = {"X-Cinematch-Session": "bob-owned-session-key-xx"}


def app_for(tmp_path: Path) -> object:
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'groups.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
        )
    )


def test_group_session_votes_consensus_and_personal_isolation(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        with client.app.state.session_factory() as db:
            db.add_all(
                [
                    Movie(id=1, title="Shared", genres=["Drama"]),
                    Movie(id=2, title="Alternative", genres=["Comedy"]),
                    Movie(id=3, title="Third", genres=["Sci-Fi"]),
                ]
            )
            db.commit()
        alice = client.post("/api/profiles", headers=A, json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/profiles", headers=B, json={"name": "Bob"}).json()["id"]
        group = client.post(
            "/api/groups", headers=A, json={"name": "Friday", "profile_id": alice}
        ).json()
        invite = client.post(
            f"/api/groups/{group['id']}/invites",
            headers=A,
            json={"profile_id": alice},
        ).json()
        client.post(
            f"/api/group-invites/{invite['token']}/join",
            headers=B,
            json={"profile_id": bob},
        )
        session = client.post(
            f"/api/groups/{group['id']}/sessions",
            headers=A,
            json={
                "requester_profile_id": alice,
                "participant_profile_ids": [alice, bob],
                "consensus_policy": "majority",
                "ranking_algorithm": "fair_consensus",
            },
        )
        assert session.status_code == 201
        session_id = session.json()["id"]
        recommendations = client.get(
            f"/api/sessions/{session_id}/recommendations",
            headers=A,
            params={"profile_id": alice},
        )
        assert recommendations.status_code == 200
        movie_id = recommendations.json()[0]["movie"]["id"]
        before = client.get(f"/api/profiles/{alice}/recommendations", headers=A).json()
        for profile_id, headers, suffix in ((alice, A, "alice"), (bob, B, "bob")):
            vote = client.post(
                f"/api/sessions/{session_id}/votes",
                headers=headers,
                json={
                    "profile_id": profile_id,
                    "movie_id": movie_id,
                    "action": "like",
                    "request_id": f"group-vote-{suffix}",
                },
            )
            assert vote.status_code == 200
        finalized = client.post(
            f"/api/sessions/{session_id}/finalize",
            headers=A,
            json={"requester_profile_id": alice, "movie_id": movie_id},
        )
        assert finalized.status_code == 200
        assert finalized.json()["selected_movie_id"] == movie_id
        after = client.get(f"/api/profiles/{alice}/recommendations", headers=A).json()
        assert [row["movie"]["id"] for row in before] == [row["movie"]["id"] for row in after]
        assert (
            client.post(
                f"/api/sessions/{session_id}/satisfaction",
                headers=B,
                json={"profile_id": bob, "satisfaction": 0.8, "accepted": True},
            ).status_code
            == 204
        )


def test_fair_group_algorithms_are_hand_checkable() -> None:
    scores = [0.8, 0.4]
    assert aggregate_group_scores(scores, "average") == pytest.approx(0.6)
    assert aggregate_group_scores(scores, "least_misery") == pytest.approx(0.4)
    assert aggregate_group_scores(scores, "fair_consensus") == pytest.approx(0.47)
    assert aggregate_group_scores(scores, "nash") == pytest.approx((0.8 * 0.4) ** 0.5)
