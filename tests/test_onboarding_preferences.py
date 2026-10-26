from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.db import Base, make_engine, make_session_factory
from backend.models import Movie


def app_for(tmp_path: Path) -> object:
    engine = make_engine(f"sqlite:///{tmp_path / 'onboarding.db'}")
    Base.metadata.create_all(engine)
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'onboarding.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
            jwt_secret="test-secret-that-is-long-and-stable",
        )
    )


def _register(client: TestClient) -> tuple[str, int]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": "taste",
            "email": "taste@example.com",
            "password": "correct-horse-battery",
            "profile_name": "Taste",
        },
    )
    assert response.status_code == 201
    profile_id = client.get(
        "/api/profiles",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    ).json()[0]["id"]
    return response.json()["access_token"], profile_id


def test_onboarding_preferences_seed_recommendations(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        token, profile_id = _register(client)
        headers = {"Authorization": f"Bearer {token}"}
        factory = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'onboarding.db'}"))
        with factory() as session:
            session.add_all(
                [
                    Movie(id=1, title="Space One", year=2010, genres=["Sci-Fi"], popularity_score=9.0),
                    Movie(id=2, title="Quiet Drama", year=2011, genres=["Drama"], popularity_score=1.0),
                ]
            )
            session.commit()

        empty = client.put(
            f"/api/profiles/{profile_id}/onboarding-preferences",
            headers=headers,
            json={"preferred_genres": [], "disliked_genres": []},
        )
        assert empty.status_code == 422

        saved = client.put(
            f"/api/profiles/{profile_id}/onboarding-preferences",
            headers=headers,
            json={"preferred_genres": ["Drama"], "disliked_genres": ["Sci-Fi"]},
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["seed_preferred_genres"] == ["Drama"]
        assert body["seed_disliked_genres"] == ["Sci-Fi"]

        recommendations = client.get(
            f"/api/profiles/{profile_id}/recommendations?limit=5",
            headers=headers,
        ).json()
        assert recommendations
        assert recommendations[0]["movie"]["title"] == "Quiet Drama"
        assert "selected" in recommendations[0]["reason"].lower() or "Drama" in recommendations[0]["reason"]
