import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Scan, Ticket, Volunteer
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


def issue_demo_ticket(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/tickets",
        json={
            "event_id": 1,
            "attendee_name": "Riya Sen",
            "attendee_contact": "riya@example.edu",
            "tier": "premium",
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()


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


def test_scan_endpoint_requires_scanner_role() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    admin_headers = auth_headers(client, "admin", "admin123")
    ticket = issue_demo_ticket(client, user_headers)
    payload = {"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1}

    assert client.post("/scans", json=payload).status_code == 401
    assert client.post("/scans", json=payload, headers=user_headers).status_code == 403
    assert client.post("/scans", json=payload, headers=admin_headers).status_code == 403


def test_scanner_accepts_valid_signed_ticket() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = issue_demo_ticket(client, user_headers)

    response = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "valid"
    assert body["ticket_id"] == ticket["ticket_id"]
    assert body["attendee_name"] == "Riya Sen"
    assert body["tier"] == "premium"
    assert body["seat_number"] == "PRE-001"

    with SessionLocal() as db:
        assert db.get(Ticket, ticket["ticket_id"]).status == "used"
        assert db.query(Scan).filter(Scan.ticket_id == ticket["ticket_id"], Scan.result == "valid").count() == 1


def test_scanner_rejects_invalid_signature_and_missing_ticket() -> None:
    reset_database()
    client = TestClient(app)
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    invalid_signature = client.post(
        "/scans",
        json={"qr_signature": "TX-999.not-a-valid-signature", "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    missing_ticket = client.post(
        "/scans",
        json={"ticket_id": 999, "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )

    assert invalid_signature.status_code == 200
    assert invalid_signature.json()["result"] == "invalid"
    assert missing_ticket.status_code == 404
    assert missing_ticket.json()["detail"] == "Ticket not found"


def test_scanner_rejects_revoked_ticket_and_records_attempt() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = issue_demo_ticket(client, user_headers)

    with SessionLocal() as db:
        stored_ticket = db.get(Ticket, ticket["ticket_id"])
        stored_ticket.status = "revoked"
        db.commit()

    response = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )

    assert response.status_code == 200
    assert response.json()["result"] == "invalid"
    assert response.json()["message"] == "Ticket has been revoked."
    with SessionLocal() as db:
        assert db.query(Scan).filter(Scan.ticket_id == ticket["ticket_id"], Scan.result == "invalid").count() == 1


def test_scanner_rejects_wrong_event_and_volunteer_assignment() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    admin_headers = auth_headers(client, "admin", "admin123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = issue_demo_ticket(client, user_headers)

    event = client.post(
        "/events",
        json={
            "title": "Robotics Expo",
            "description": "Student robotics demonstrations.",
            "date_time": "2026-10-01T10:00:00",
            "venue": "Lab Block",
            "capacity": 100,
        },
        headers=admin_headers,
    ).json()
    gate = client.post(
        "/gates",
        json={
            "event_id": event["id"],
            "name": "Lab Gate",
            "location": "Block A",
            "volunteer_name": "Lab Volunteer",
        },
        headers=admin_headers,
    ).json()

    with SessionLocal() as db:
        lab_volunteer = db.query(Volunteer).filter(Volunteer.gate_id == gate["id"]).one()
        lab_volunteer_id = lab_volunteer.id

    wrong_event = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": gate["id"], "volunteer_id": lab_volunteer_id},
        headers=scanner_headers,
    )
    wrong_assignment = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 3},
        headers=scanner_headers,
    )

    assert wrong_event.status_code == 200
    assert wrong_event.json()["result"] == "invalid"
    assert wrong_event.json()["message"] == "Ticket does not belong to this gate's event."
    assert wrong_assignment.status_code == 403


def test_scanner_reports_missing_gate_and_volunteer() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = issue_demo_ticket(client, user_headers)

    missing_gate = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 999, "volunteer_id": 1},
        headers=scanner_headers,
    )
    missing_volunteer = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 999},
        headers=scanner_headers,
    )

    assert missing_gate.status_code == 404
    assert missing_gate.json()["detail"] == "Gate not found"
    assert missing_volunteer.status_code == 404
    assert missing_volunteer.json()["detail"] == "Volunteer not found"


def test_valid_scan_then_duplicate_scan() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = client.post(
        "/tickets",
        json={"event_id": 1, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu"},
        headers=user_headers,
    ).json()

    first_scan = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    second_scan = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )

    assert first_scan.status_code == 200
    first_body = first_scan.json()
    assert first_body["result"] == "valid"
    assert second_scan.status_code == 200
    duplicate_body = second_scan.json()
    assert duplicate_body["result"] == "duplicate"
    assert duplicate_body["prior_scan"]["gate_name"] == "Main Gate"
    assert duplicate_body["prior_scan"]["timestamp"] is not None
    assert duplicate_body["attendee_name"] == "Riya Sen"
    assert duplicate_body["tier"] == "general"
    assert duplicate_body["seat_number"] == "GEN-001"

    with SessionLocal() as db:
        scans = db.query(Scan).filter(Scan.ticket_id == ticket["ticket_id"]).all()
        assert len(scans) == 2
        valid_scans = [s for s in scans if s.result == "valid"]
        duplicate_scans = [s for s in scans if s.result == "duplicate"]
        assert len(valid_scans) == 1
        assert len(duplicate_scans) == 1
