from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


def app_for(tmp_path: Path, **overrides: object) -> object:
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
            jwt_secret="test-secret-that-is-long-and-stable",
            **overrides,
        )
    )


def register(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct-horse-battery",
            "profile_name": "Alice",
        },
    )
    assert response.status_code == 201
    return {
        "Authorization": f"Bearer {response.json()['access_token']}",
    }


def test_username_email_and_password_changes_require_the_emailed_code(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        headers = register(client)
        rejected = client.post(
            "/api/users/me/verification",
            headers=headers,
            json={
                "purpose": "username",
                "username": "alice-new",
                "current_password": "wrong-password-value",
            },
        )
        assert rejected.status_code == 403

        username_code = client.post(
            "/api/users/me/verification",
            headers=headers,
            json={
                "purpose": "username",
                "username": "alice-new",
                "current_password": "correct-horse-battery",
            },
        )
        assert username_code.status_code == 200
        issued = username_code.json()
        assert issued["sent_to"] == "alice@example.com"
        assert issued["delivery"] == "preview"
        assert issued["preview_code"]

        wrong_code = "000000" if issued["preview_code"] != "000000" else "000001"
        wrong = client.post(
            "/api/users/me/verification/confirm",
            headers=headers,
            json={"purpose": "username", "code": wrong_code},
        )
        assert wrong.status_code == 400
        assert client.get("/api/users/me", headers=headers).json()["username"] == "alice"

        confirmed = client.post(
            "/api/users/me/verification/confirm",
            headers=headers,
            json={"purpose": "username", "code": issued["preview_code"]},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["username"] == "alice-new"

        email_code = client.post(
            "/api/users/me/verification",
            headers=headers,
            json={
                "purpose": "email",
                "email": "alice.new@example.com",
                "current_password": "correct-horse-battery",
            },
        )
        assert email_code.status_code == 200
        assert email_code.json()["sent_to"] == "alice.new@example.com"
        client.post(
            "/api/users/me/verification/confirm",
            headers=headers,
            json={"purpose": "email", "code": email_code.json()["preview_code"]},
        )
        updated_email = client.get("/api/users/me", headers=headers).json()["email"]
        assert updated_email == "alice.new@example.com"

        password_code = client.post(
            "/api/users/me/verification",
            headers=headers,
            json={
                "purpose": "password",
                "password": "a-brand-new-secret",
                "current_password": "correct-horse-battery",
            },
        )
        assert password_code.json()["sent_to"] == "alice.new@example.com"
        assert (
            client.post(
                "/api/users/me/verification/confirm",
                headers=headers,
                json={"purpose": "password", "code": password_code.json()["preview_code"]},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "alice.new@example.com", "password": "correct-horse-battery"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "alice.new@example.com", "password": "a-brand-new-secret"},
            ).status_code
            == 200
        )


def test_unconfigured_mail_is_not_a_preview_when_smtp_is_set(tmp_path: Path) -> None:
    with TestClient(
        app_for(tmp_path, smtp_host="127.0.0.1", smtp_port=1, smtp_tls=False)
    ) as client:
        headers = register(client)
        response = client.post(
            "/api/users/me/verification",
            headers=headers,
            json={
                "purpose": "username",
                "username": "alice-new",
                "current_password": "correct-horse-battery",
            },
        )
        assert response.status_code == 502
        assert client.get("/api/users/me", headers=headers).json()["username"] == "alice"
