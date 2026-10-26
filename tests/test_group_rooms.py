from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings

A = {"X-Cinematch-Session": "alice-owned-session-key"}
B = {"X-Cinematch-Session": "bob-owned-session-key-xx"}
C = {"X-Cinematch-Session": "carol-owned-session-key"}


def test_each_invite_link_opens_a_separate_room(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'rooms.db'}",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "artifacts",
            cors_origins="http://test",
            auto_create_schema=True,
            poster_lookup=False,
        )
    )
    with TestClient(app) as client:
        alice = client.post("/api/profiles", headers=A, json={"name": "Alice"}).json()["id"]
        bob = client.post("/api/profiles", headers=B, json={"name": "Bob"}).json()["id"]
        carol = client.post("/api/profiles", headers=C, json={"name": "Carol"}).json()["id"]
        group = client.post(
            "/api/groups", headers=A, json={"name": "Friday", "profile_id": alice}
        ).json()
        alice_invite = client.post(
            f"/api/groups/{group['id']}/invites",
            headers=A,
            json={"profile_id": alice},
        ).json()
        second_alice = client.post(
            f"/api/groups/{group['id']}/invites",
            headers=A,
            json={"profile_id": alice},
        ).json()
        assert alice_invite["room_name"] == "Alice's room"
        assert second_alice["room_name"] == "Alice's room 2"
        assert alice_invite["room_id"] != second_alice["room_id"]

        joined = client.post(
            f"/api/group-invites/{alice_invite['token']}/join",
            headers=B,
            json={"profile_id": bob},
        ).json()
        assert joined["joined_room_name"] == "Alice's room"
        bob_invite = client.post(
            f"/api/groups/{group['id']}/invites",
            headers=B,
            json={"profile_id": bob},
        ).json()
        carol_join = client.post(
            f"/api/group-invites/{bob_invite['token']}/join",
            headers=C,
            json={"profile_id": carol},
        ).json()
        assert carol_join["joined_room_name"] == "Bob's room"

        alice_rooms = client.get(
            f"/api/groups/{group['id']}/rooms", headers=A, params={"profile_id": alice}
        ).json()
        bob_rooms = client.get(
            f"/api/groups/{group['id']}/rooms", headers=B, params={"profile_id": bob}
        ).json()
        carol_rooms = client.get(
            f"/api/groups/{group['id']}/rooms", headers=C, params={"profile_id": carol}
        ).json()
        assert [room["name"] for room in alice_rooms] == [
            "Alice's room",
            "Alice's room 2",
            "Bob's room",
        ]
        assert [room["name"] for room in bob_rooms] == ["Alice's room", "Bob's room"]
        assert [room["name"] for room in carol_rooms] == ["Bob's room"]
        alice_room = next(room for room in alice_rooms if room["name"] == "Alice's room")
        bob_room = next(room for room in bob_rooms if room["name"] == "Bob's room")
        assert {member["name"] for member in alice_room["members"]} == {"Alice", "Bob"}
        assert {member["name"] for member in bob_room["members"]} == {"Bob", "Carol"}

        denied = client.post(
            f"/api/groups/{group['id']}/sessions",
            headers=A,
            json={
                "requester_profile_id": alice,
                "room_id": bob_room["id"],
                "participant_profile_ids": [bob, carol],
            },
        )
        assert denied.status_code == 403
        started = client.post(
            f"/api/groups/{group['id']}/sessions",
            headers=B,
            json={
                "requester_profile_id": bob,
                "room_id": bob_room["id"],
                "participant_profile_ids": [bob, carol],
            },
        )
        assert started.status_code == 201
        assert started.json()["room_id"] == bob_room["id"]
        carol_sessions = client.get(
            f"/api/groups/{group['id']}/sessions",
            headers=C,
            params={"profile_id": carol},
        ).json()
        assert [session["room_id"] for session in carol_sessions] == [bob_room["id"]]
        hidden = client.post(
            f"/api/groups/{group['id']}/rooms/{alice_room['id']}/recommendations",
            headers=C,
            json={"requester_profile_id": carol, "strategy": "balanced"},
        )
        assert hidden.status_code == 404
