from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


def app_for(tmp_path: Path) -> object:
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
            jwt_secret="test-secret-that-is-long-and-stable",
        )
    )


def test_register_login_refresh_logout_and_protected_profiles(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "username": "alice",
                "email": "ALICE@example.com",
                "password": "correct-horse-battery",
                "profile_name": "Alice",
            },
        )
        assert registration.status_code == 201
        assert registration.json()["user"]["email"] == "alice@example.com"
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/users/me", headers=headers).status_code == 200
        profiles = client.get("/api/profiles", headers=headers)
        assert [profile["name"] for profile in profiles.json()] == ["Alice"]

        assert (
            client.post(
                "/api/auth/login",
                json={"email": "alice@example.com", "password": "wrong"},
            ).status_code
            == 401
        )
        login = client.post(
            "/api/auth/login",
            json={
                "email": "alice@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert login.status_code == 200
        refreshed = client.post("/api/auth/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] != login.json()["access_token"]
        assert client.post("/api/auth/logout").status_code == 204
        assert client.post("/api/auth/refresh").status_code == 401


def test_legacy_profile_can_be_claimed_once(tmp_path: Path) -> None:
    legacy_key = "legacy-session-key-at-least-sixteen"
    with TestClient(app_for(tmp_path)) as client:
        legacy = client.post(
            "/api/profiles",
            json={"name": "Legacy"},
            headers={"X-Cinematch-Session": legacy_key},
        )
        registration = client.post(
            "/api/auth/register",
            json={
                "username": "owner",
                "email": "owner@example.com",
                "password": "correct-horse-battery",
                "profile_name": "Owner",
            },
        )
        headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
        claimed = client.post(
            "/api/users/me/claim-legacy-profile",
            headers=headers,
            json={
                "profile_id": legacy.json()["id"],
                "legacy_session_key": legacy_key,
            },
        )
        assert claimed.status_code == 200
        assert {
            profile["name"] for profile in client.get("/api/profiles", headers=headers).json()
        } == {
            "Legacy",
            "Owner",
        }
        assert (
            client.get(
                f"/api/profiles/{legacy.json()['id']}/recommendations",
                headers={"X-Cinematch-Session": legacy_key},
            ).status_code
            == 403
        )
