from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.models import Movie
from backend.tmdb import TMDBClient

SESSION_HEADERS = {"X-Cinematch-Session": "test-session-owner-key"}


def settings_for(tmp_path: Path, read_only: bool = False) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "artifacts",
        read_only=read_only,
        cors_origins="http://test",
        auto_create_schema=True,
        poster_lookup=False,
    )


def seed_movies(client: TestClient) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        session.add_all(
            [
                Movie(id=1, title="Space One", year=2000, genres=["Sci-Fi", "Adventure"]),
                Movie(id=2, title="Space Two", year=2001, genres=["Sci-Fi", "Drama"]),
                Movie(id=3, title="Quiet Drama", year=2002, genres=["Drama"]),
                Movie(id=4, title="Big Laugh", year=2003, genres=["Comedy"]),
            ]
        )
        session.commit()


def test_end_to_end_api_flow(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        client.headers.update(SESSION_HEADERS)
        seed_movies(client)
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200

        alice = client.post("/api/profiles", json={"name": "Alice"})
        bob = client.post("/api/profiles", json={"name": "Bob"})
        assert alice.status_code == 201
        assert client.post("/api/profiles", json={"name": "Alice"}).status_code == 409
        alice_id, bob_id = alice.json()["id"], bob.json()["id"]

        search = client.get("/api/movies", params={"q": "Space", "page_size": 1}).json()
        assert search["total"] == 2
        assert len(search["items"]) == 1

        rating_url = f"/api/profiles/{alice_id}/ratings/1"
        first = client.put(rating_url, json={"value": 4.0})
        second = client.put(rating_url, json={"value": 5.0})
        assert first.status_code == 200
        assert second.json()["value"] == 5.0

        assert (
            client.put(
                f"/api/profiles/{alice_id}/interactions/watched/2", json={"active": True}
            ).status_code
            == 204
        )
        watched = client.get(f"/api/profiles/{alice_id}/interactions/watched").json()
        assert [movie["id"] for movie in watched] == [2]

        recommendations = client.get(
            f"/api/profiles/{alice_id}/recommendations", params={"limit": 10}
        ).json()
        assert {item["movie"]["id"] for item in recommendations}.isdisjoint({1, 2})
        assert all(item["reason"] for item in recommendations)

        similar = client.get("/api/movies/1/similar").json()
        assert similar[0]["movie"]["id"] == 2

        for profile_id in (bob_id,):
            assert (
                client.put(f"/api/profiles/{profile_id}/ratings/3", json={"value": 4.5}).status_code
                == 200
            )
        group = client.post(
            "/api/recommendations/group",
            json={"profile_ids": [alice_id, bob_id], "limit": 2},
        )
        assert group.status_code == 200
        assert len(group.json()) <= 2


def test_validation_and_read_only_guard(tmp_path: Path) -> None:
    writable = create_app(settings_for(tmp_path))
    with TestClient(writable) as client:
        client.headers.update(SESSION_HEADERS)
        seed_movies(client)
        assert client.get("/api/movies", params={"page_size": 101}).status_code == 422
        assert client.post("/api/profiles", json={"name": " "}).status_code == 422
        assert (
            client.post(
                "/api/profiles",
                json={"name": "No owner"},
                headers={"X-Cinematch-Session": ""},
            ).status_code
            == 401
        )

    read_only = create_app(settings_for(tmp_path, read_only=True))
    with TestClient(read_only) as client:
        response = client.post("/api/profiles", json={"name": "Blocked"})
        assert response.status_code == 403
        assert response.headers["x-request-id"]
        assert client.get("/api/movies").status_code == 200


def test_tmdb_is_optional(tmp_path: Path) -> None:
    client = TMDBClient(None, tmp_path / "cache")
    assert not client.enabled
    assert client.movie(862) is None
