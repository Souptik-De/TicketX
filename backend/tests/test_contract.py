import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.seed import seed_reference_data


def teardown_module() -> None:
    engine.dispose()
    TEST_DATABASE_PATH.unlink(missing_ok=True)


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_reference_data(db)
    finally:
        db.close()


def auth_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_returns_role_and_token() -> None:
    reset_database()
    client = TestClient(app)

    response = client.post("/auth/login", json={"username": "admin", "password": "admin123"})

    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["user"]["role"] == "admin"


def test_events_are_public_but_ticketing_requires_login() -> None:
    reset_database()
    client = TestClient(app)

    events_response = client.get("/events")
    ticket_response = client.post(
        "/tickets",
        json={"event_id": 1, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu"},
    )

    assert events_response.status_code == 200
    assert events_response.json()[0]["title"] == "Techno Cultural Fest 2026"
    assert ticket_response.status_code == 401


def test_ticket_can_be_issued() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")

    response = client.post(
        "/tickets",
        json={
            "event_id": 1,
            "attendee_name": "Riya Sen",
            "attendee_contact": "riya@example.edu",
            "campus_id": "CAMP-2026-042",
            "tier": "general",
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["ticket_id"] == 1
    assert body["qr_signature"].startswith("TX-1.")
    assert body["seat_number"] == "GEN-001"
    assert body["status"] == "issued"


def test_seat_numbers_are_allocated_within_each_tier() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")

    seats = []
    for index, tier in enumerate(["general", "general", "premium", "vip"], start=1):
        response = client.post(
            "/tickets",
            json={
                "event_id": 1,
                "attendee_name": f"Attendee {index}",
                "attendee_contact": f"attendee{index}@example.edu",
                "tier": tier,
            },
            headers=headers,
        )
        assert response.status_code == 201
        seats.append(response.json()["seat_number"])

    assert seats == ["GEN-001", "GEN-002", "PRE-001", "VIP-001"]


def test_roles_cannot_use_another_workspace_api() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    ticket_payload = {
        "event_id": 1,
        "attendee_name": "Riya Sen",
        "attendee_contact": "riya@example.edu",
    }

    assert client.post("/tickets", json=ticket_payload, headers=admin_headers).status_code == 403
    assert client.post("/events", json={
        "title": "Blocked Event",
        "description": "Not allowed",
        "date_time": "2026-10-01T10:00:00",
        "venue": "Hall A",
        "capacity": 50,
    }, headers=scanner_headers).status_code == 403


def test_admin_can_create_event() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "admin", "admin123")

    response = client.post(
        "/events",
        json={
            "title": "Freshers Night 2026",
            "description": "An evening of live performances and student showcases.",
            "date_time": "2026-09-18T18:30:00",
            "venue": "Seminar Hall",
            "capacity": 250,
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Freshers Night 2026"
    assert body["description"] == "An evening of live performances and student showcases."
    assert body["capacity"] == 250
    assert body["issued_count"] == 0


def test_admin_can_create_gate_for_event() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "admin", "admin123")

    response = client.post(
        "/gates",
        json={
            "event_id": 1,
            "name": "VIP Gate",
            "location": "Auditorium east entrance",
            "volunteer_name": "New Volunteer",
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["event_id"] == 1
    assert body["name"] == "VIP Gate"


def test_ticket_details_match_contract() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    created = client.post(
        "/tickets",
        json={"event_id": 1, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu"},
        headers=headers,
    ).json()

    response = client.get(f"/tickets/{created['ticket_id']}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == created["ticket_id"]
    assert body["event"]["title"] == "Techno Cultural Fest 2026"
    assert body["attendee"]["name"] == "Riya Sen"


def test_ticket_issuance_validations() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    # Create small event with capacity 1
    small_event = client.post(
        "/events",
        json={
            "title": "Tiny Workshop",
            "description": "Limited seats",
            "date_time": "2026-10-01T10:00:00",
            "venue": "Room 101",
            "capacity": 1,
        },
        headers=admin_headers,
    ).json()

    # Missing event -> 404
    res_404 = client.post(
        "/tickets",
        json={"event_id": 999, "attendee_name": "User 1", "attendee_contact": "u1@example.com"},
        headers=user_headers,
    )
    assert res_404.status_code == 404

    # Invalid tier -> 400
    res_400 = client.post(
        "/tickets",
        json={"event_id": small_event["id"], "attendee_name": "User 1", "attendee_contact": "u1@example.com", "tier": "super-vip"},
        headers=user_headers,
    )
    assert res_400.status_code == 400

    # First ticket -> 201
    res_201 = client.post(
        "/tickets",
        json={"event_id": small_event["id"], "attendee_name": "User 1", "attendee_contact": "u1@example.com", "tier": "general"},
        headers=user_headers,
    )
    assert res_201.status_code == 201

    # Second ticket for capacity=1 event -> 409 Conflict
    res_409 = client.post(
        "/tickets",
        json={"event_id": small_event["id"], "attendee_name": "User 2", "attendee_contact": "u2@example.com", "tier": "general"},
        headers=user_headers,
    )
    assert res_409.status_code == 409
