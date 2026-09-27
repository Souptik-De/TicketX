import csv
import io
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app import database as database_module
from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Scan, Ticket, Volunteer, WaitlistEntry
from backend.app.security import ticket_id_from_signature
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
    assert res_201.json()["outcome"] == "ticketed"

    # Second ticket for capacity=1 event -> joins waitlist
    res_waitlist = client.post(
        "/tickets",
        json={"event_id": small_event["id"], "attendee_name": "User 2", "attendee_contact": "u2@example.com", "tier": "general"},
        headers=user_headers,
    )
    assert res_waitlist.status_code == 201
    waitlist_data = res_waitlist.json()
    assert waitlist_data["outcome"] == "waitlisted"
    assert waitlist_data["waitlisted"] is True
    assert waitlist_data["position"] == 1
    assert waitlist_data["event_id"] == small_event["id"]


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


def test_gate_status_counts_valid_scans_only() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    ticket = client.post(
        "/tickets",
        json={"event_id": 1, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu"},
        headers=user_headers,
    ).json()

    # Initial gate status: 0 valid scans and last_synced is None
    initial_res = client.get("/gates/status", headers=scanner_headers)
    assert initial_res.status_code == 200
    initial_gates = initial_res.json()
    initial_main = next(gate for gate in initial_gates if gate["name"] == "Main Gate")
    assert initial_main["scanned_count"] == 0
    assert initial_main["last_synced"] is None
    assert initial_main["online"] is True

    # Valid scan
    first_scan = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert first_scan.status_code == 200
    assert first_scan.json()["result"] == "valid"

    # Duplicate scan attempt
    second_scan = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert second_scan.status_code == 200
    assert second_scan.json()["result"] == "duplicate"

    # Invalid scan attempt
    invalid_scan = client.post(
        "/scans",
        json={"qr_signature": "TX-999.invalid-sig", "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert invalid_scan.status_code == 200
    assert invalid_scan.json()["result"] == "invalid"

    # Verify status: only valid scan is counted, and last_synced is present
    response = client.get("/gates/status", headers=scanner_headers)
    assert response.status_code == 200
    gates = response.json()
    main_gate = next(gate for gate in gates if gate["name"] == "Main Gate")
    assert main_gate["scanned_count"] == 1
    assert main_gate["online"] is True
    assert main_gate["last_synced"] is not None


def test_gate_status_can_filter_by_event() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
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
    client.post(
        "/gates",
        json={
            "event_id": event["id"],
            "name": "Lab Gate",
            "location": "Block A",
            "volunteer_name": "Lab Volunteer",
        },
        headers=admin_headers,
    )

    response = client.get(f"/gates/status?event_id={event['id']}", headers=admin_headers)

    assert response.status_code == 200
    gates = response.json()
    assert len(gates) == 1
    assert gates[0]["name"] == "Lab Gate"
    assert gates[0]["event_id"] == event["id"]


def test_gate_status_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    admin_headers = auth_headers(client, "admin", "admin123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    # Unauthenticated access rejected
    assert client.get("/gates/status").status_code == 401
    # Attendee / normal user role rejected
    assert client.get("/gates/status", headers=user_headers).status_code == 403
    # Admin and scanner roles allowed
    assert client.get("/gates/status", headers=admin_headers).status_code == 200
    assert client.get("/gates/status", headers=scanner_headers).status_code == 200


def test_registration_allowed_roles() -> None:
    reset_database()
    client = TestClient(app)

    # User registration succeeds
    user_res = client.post(
        "/auth/register",
        json={
            "username": "newuser",
            "password": "password123",
            "display_name": "New User",
            "role": "user",
        },
    )
    assert user_res.status_code == 201
    assert user_res.json()["user"]["role"] == "user"
    assert user_res.json()["user"]["username"] == "newuser"

    # Scanner registration succeeds
    scanner_res = client.post(
        "/auth/register",
        json={
            "username": "newscanner",
            "password": "password123",
            "display_name": "New Scanner",
            "role": "scanner",
        },
    )
    assert scanner_res.status_code == 201
    assert scanner_res.json()["user"]["role"] == "scanner"

    # Duplicate username fails
    dup_res = client.post(
        "/auth/register",
        json={
            "username": "newuser",
            "password": "password123",
            "display_name": "Duplicate User",
            "role": "user",
        },
    )
    assert dup_res.status_code == 409


def test_admin_registration_rejected() -> None:
    reset_database()
    client = TestClient(app)

    # Admin role is rejected (FastAPI schema rejects invalid role value with 422)
    admin_res = client.post(
        "/auth/register",
        json={
            "username": "newadmin",
            "password": "password123",
            "display_name": "New Admin",
            "role": "admin",
        },
    )
    assert admin_res.status_code in (422, 403)


def test_revoke_ticket_and_scan_rejection() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    # Given: an issued ticket for an event
    ticket = issue_demo_ticket(client, user_headers)
    ticket_id = ticket["ticket_id"]

    # When: admin revokes the ticket via POST /tickets/{ticket_id}/revoke
    revoke_response = client.post(f"/tickets/{ticket_id}/revoke", headers=admin_headers)
    assert revoke_response.status_code == 200
    revoke_data = revoke_response.json()
    assert revoke_data["ticket_id"] == ticket_id
    assert revoke_data["status"] == "revoked"
    assert revoke_data["revoked_at"] is not None

    # Then: attempting to scan it is rejected specifically because it is revoked
    scan_response = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert scan_response.status_code == 200
    scan_data = scan_response.json()
    assert scan_data["result"] == "invalid"
    assert "revoked" in scan_data["message"].lower()
    # Specifically not a duplicate or invalid signature rejection
    assert scan_data["result"] != "duplicate"
    assert "duplicate" not in scan_data["message"].lower()
    assert "already used" not in scan_data["message"].lower()
    assert "could not be verified" not in scan_data["message"].lower()

    # And: a second revoke attempt on the same ticket returns 409
    second_revoke = client.post(f"/tickets/{ticket_id}/revoke", headers=admin_headers)
    assert second_revoke.status_code == 409
    assert "already revoked" in second_revoke.json()["detail"].lower()


def test_revoked_ticket_frees_capacity_in_event_listing() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    # Given: a small event holding exactly one ticket
    event = client.post(
        "/events",
        json={
            "title": "Capacity One",
            "description": "",
            "date_time": "2026-10-01T10:00:00",
            "venue": "Hall A",
            "capacity": 1,
        },
        headers=admin_headers,
    ).json()
    event_id = event["id"]
    first = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Asha Roy", "attendee_contact": "asha@example.edu"},
        headers=user_headers,
    ).json()
    assert first["outcome"] == "ticketed"

    # When: the ticket is revoked
    revoked = client.post(f"/tickets/{first['ticket_id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200

    # Then: the public listing reports the seat as free, not as issued
    listing = client.get("/events").json()
    listed = next(item for item in listing if item["id"] == event_id)
    assert listed["issued_count"] == 0
    assert listed["revoked_count"] == 1

    # And: the freed seat is actually issuable rather than waitlisted
    second = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Bo Das", "attendee_contact": "bo@example.edu"},
        headers=user_headers,
    ).json()
    assert second["outcome"] == "ticketed"
    assert second["seat_number"]


def test_waitlist_join_when_event_at_capacity() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    # Given: an event at capacity (issue tickets until full)
    event = client.post(
        "/events",
        json={
            "title": "Sold Out Tech Talk",
            "description": "Exclusive event with capacity of 1.",
            "date_time": "2026-11-15T18:00:00",
            "venue": "Main Auditorium",
            "capacity": 1,
        },
        headers=admin_headers,
    ).json()

    initial_ticket = client.post(
        "/tickets",
        json={
            "event_id": event["id"],
            "attendee_name": "First Attendee",
            "attendee_contact": "first@example.edu",
            "tier": "general",
        },
        headers=user_headers,
    )
    assert initial_ticket.status_code == 201
    assert initial_ticket.json()["outcome"] == "ticketed"

    # When: one more attendee attempts to book
    first_waitlist_attempt = client.post(
        "/tickets",
        json={
            "event_id": event["id"],
            "attendee_name": "Waitlisted Attendee 1",
            "attendee_contact": "waitlist1@example.edu",
            "campus_id": "CAMP-WAIT-01",
            "tier": "general",
        },
        headers=user_headers,
    )

    # Then: assert the response has outcome="waitlisted" and position=1 (first person on this event's waitlist)
    assert first_waitlist_attempt.status_code == 201
    first_body = first_waitlist_attempt.json()
    assert first_body["outcome"] == "waitlisted"
    assert first_body["waitlisted"] is True
    assert first_body["position"] == 1
    assert first_body["event_id"] == event["id"]

    # And: issue a second waitlisted attempt, assert position=2
    second_waitlist_attempt = client.post(
        "/tickets",
        json={
            "event_id": event["id"],
            "attendee_name": "Waitlisted Attendee 2",
            "attendee_contact": "waitlist2@example.edu",
            "campus_id": "CAMP-WAIT-02",
            "tier": "vip",
        },
        headers=user_headers,
    )
    assert second_waitlist_attempt.status_code == 201
    second_body = second_waitlist_attempt.json()
    assert second_body["outcome"] == "waitlisted"
    assert second_body["waitlisted"] is True
    assert second_body["position"] == 2
    assert second_body["event_id"] == event["id"]

    # Verify rows in database
    with SessionLocal() as db:
        entries = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.event_id == event["id"])
            .order_by(WaitlistEntry.position.asc())
            .all()
        )
        assert len(entries) == 2
        assert entries[0].position == 1
        assert entries[0].attendee_name == "Waitlisted Attendee 1"
        assert entries[0].attendee_contact == "waitlist1@example.edu"
        assert entries[0].campus_id == "CAMP-WAIT-01"
        assert entries[0].tier == "general"
        assert entries[0].status == "waiting"

        assert entries[1].position == 2
        assert entries[1].attendee_name == "Waitlisted Attendee 2"
        assert entries[1].attendee_contact == "waitlist2@example.edu"
        assert entries[1].campus_id == "CAMP-WAIT-02"
        assert entries[1].tier == "vip"
        assert entries[1].status == "waiting"


def test_waitlist_auto_promotion_on_revoke() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    # Given: an event at capacity with two waitlisted attendees (positions 1 and 2)
    event = client.post(
        "/events",
        json={
            "title": "Capacity One Keynote",
            "description": "Event with capacity of 1 for testing auto-promotion.",
            "date_time": "2026-11-20T10:00:00",
            "venue": "Main Auditorium",
            "capacity": 1,
        },
        headers=admin_headers,
    ).json()
    event_id = event["id"]

    initial_ticket_res = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Original Holder",
            "attendee_contact": "original@example.edu",
            "campus_id": "CAMP-ORIG-01",
            "tier": "general",
        },
        headers=user_headers,
    )
    assert initial_ticket_res.status_code == 201
    assert initial_ticket_res.json()["outcome"] == "ticketed"
    revoked_ticket_id = initial_ticket_res.json()["ticket_id"]

    # First waitlisted attendee (position 1) requesting VIP tier
    first_waitlist_res = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Waitlisted Attendee 1",
            "attendee_contact": "waitlist1@example.edu",
            "campus_id": "CAMP-WAIT-01",
            "tier": "vip",
        },
        headers=user_headers,
    )
    assert first_waitlist_res.status_code == 201
    assert first_waitlist_res.json()["outcome"] == "waitlisted"
    assert first_waitlist_res.json()["position"] == 1

    # Second waitlisted attendee (position 2) requesting General tier
    second_waitlist_res = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Waitlisted Attendee 2",
            "attendee_contact": "waitlist2@example.edu",
            "campus_id": "CAMP-WAIT-02",
            "tier": "general",
        },
        headers=user_headers,
    )
    assert second_waitlist_res.status_code == 201
    assert second_waitlist_res.json()["outcome"] == "waitlisted"
    assert second_waitlist_res.json()["position"] == 2

    # When: an admin revokes one issued ticket for that event
    revoke_res = client.post(f"/tickets/{revoked_ticket_id}/revoke", headers=admin_headers)
    assert revoke_res.status_code == 200
    revoke_data = revoke_res.json()

    # Then: assert the revoke response's promoted_attendee is not null and matches the FIRST waitlisted attendee (position 1)
    assert revoke_data["status"] == "revoked"
    assert revoke_data["ticket_id"] == revoked_ticket_id
    assert revoke_data["promoted_attendee"] is not None
    promoted = revoke_data["promoted_attendee"]
    assert promoted["attendee_name"] == "Waitlisted Attendee 1"
    assert promoted["tier"] == "vip"
    new_ticket_id = promoted["new_ticket_id"]
    assert new_ticket_id is not None
    assert new_ticket_id != revoked_ticket_id

    # And: assert a new Ticket row now exists for that attendee, with a valid HMAC signature
    with SessionLocal() as db:
        new_ticket = db.get(Ticket, new_ticket_id)
        assert new_ticket is not None
        assert new_ticket.event_id == event_id
        assert new_ticket.tier == "vip"
        assert new_ticket.status == "issued"
        assert new_ticket.attendee.name == "Waitlisted Attendee 1"
        assert new_ticket.qr_signature is not None
        assert new_ticket.qr_signature.startswith(f"TX-{new_ticket.id}.")
        assert ticket_id_from_signature(new_ticket.qr_signature) == new_ticket.id

        # And: assert that attendee's WaitlistEntry.status is now "promoted"
        entry_1 = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.attendee_name == "Waitlisted Attendee 1")
            .one()
        )
        assert entry_1.status == "promoted"

        # And: assert the previously-second waitlisted attendee's position is now 1, not 2
        entry_2 = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.attendee_name == "Waitlisted Attendee 2")
            .one()
        )
        assert entry_2.status == "waiting"
        assert entry_2.position == 1


def test_revoke_ticket_with_empty_waitlist_noop() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    # Given: a ticket for an event with an EMPTY waitlist
    ticket = issue_demo_ticket(client, user_headers)
    ticket_id = ticket["ticket_id"]

    # When: an admin revokes it
    revoke_response = client.post(f"/tickets/{ticket_id}/revoke", headers=admin_headers)
    assert revoke_response.status_code == 200
    revoke_data = revoke_response.json()

    # Then: assert promoted_attendee is null and behavior is otherwise identical to the Commit 1 test
    assert revoke_data["ticket_id"] == ticket_id
    assert revoke_data["status"] == "revoked"
    assert revoke_data["revoked_at"] is not None
    assert revoke_data.get("promoted_attendee") is None

    # scan rejection still works
    scan_response = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert scan_response.status_code == 200
    scan_data = scan_response.json()
    assert scan_data["result"] == "invalid"
    assert "revoked" in scan_data["message"].lower()

    # Second revoke attempt still returns 409
    second_revoke = client.post(f"/tickets/{ticket_id}/revoke", headers=admin_headers)
    assert second_revoke.status_code == 409
    assert "already revoked" in second_revoke.json()["detail"].lower()


def test_export_attendance_csv() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    event_id = 1

    # Given: an event with three tickets -- one scanned (checked in at a specific gate),
    # one never scanned (no-show), and one revoked (reusing revoke_ticket flow from ET-11)
    # 1. Scanned ticket
    res_ticket_1 = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Alice Scanned",
            "attendee_contact": "alice@example.edu",
            "campus_id": "CAMPUS-001",
            "tier": "general",
        },
        headers=user_headers,
    )
    assert res_ticket_1.status_code == 201
    ticket_1 = res_ticket_1.json()

    scan_res = client.post(
        "/scans",
        json={"qr_signature": ticket_1["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert scan_res.status_code == 200
    assert scan_res.json()["result"] == "valid"

    # 2. Never scanned ticket (no-show)
    res_ticket_2 = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Bob Noshow",
            "attendee_contact": "bob@example.edu",
            "campus_id": "CAMPUS-002",
            "tier": "premium",
        },
        headers=user_headers,
    )
    assert res_ticket_2.status_code == 201

    # 3. Revoked ticket
    res_ticket_3 = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Charlie Revoked",
            "attendee_contact": "charlie@example.edu",
            "campus_id": "CAMPUS-003",
            "tier": "vip",
        },
        headers=user_headers,
    )
    assert res_ticket_3.status_code == 201
    ticket_3 = res_ticket_3.json()

    revoke_res = client.post(f"/tickets/{ticket_3['ticket_id']}/revoke", headers=admin_headers)
    assert revoke_res.status_code == 200

    # When: an admin calls GET /events/{event_id}/export
    response = client.get(f"/events/{event_id}/export", headers=admin_headers)

    # Then: assert the response has media_type "text/csv" and a Content-Disposition header containing the expected filename
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert f'filename="event-{event_id}-attendance.csv"' in response.headers.get("content-disposition", "")

    # And: parse the returned CSV body (use Python's csv.reader on the response text) and assert:
    reader = list(csv.reader(io.StringIO(response.text)))

    header = reader[0]
    assert header == [
        "Attendee Name",
        "Contact Email",
        "Campus ID",
        "Tier",
        "Seat Number",
        "Status",
        "Gate",
        "Check-in Timestamp",
    ]

    # - locate the separator blank row dividing per-ticket rows and summary block
    blank_index = next(i for i, row in enumerate(reader[1:], start=1) if not row or not any(row))
    ticket_rows = reader[1:blank_index]
    # - exactly 3 data rows
    assert len(ticket_rows) == 3

    rows_by_name = {row[0]: row for row in ticket_rows}

    # - the checked-in row has the correct Gate and a non-empty Check-in Timestamp
    alice_row = rows_by_name["Alice Scanned"]
    assert alice_row[1] == "alice@example.edu"
    assert alice_row[2] == "CAMPUS-001"
    assert alice_row[3] == "general"
    assert alice_row[5] == "Checked in"
    assert alice_row[6] == "Main Gate"
    assert alice_row[7] != ""

    # - the no-show row has Status "No-show" and empty Gate/Timestamp
    bob_row = rows_by_name["Bob Noshow"]
    assert bob_row[1] == "bob@example.edu"
    assert bob_row[2] == "CAMPUS-002"
    assert bob_row[3] == "premium"
    assert bob_row[5] == "No-show"
    assert bob_row[6] == ""
    assert bob_row[7] == ""

    # - the revoked row has Status "Revoked" and empty Gate/Timestamp
    charlie_row = rows_by_name["Charlie Revoked"]
    assert charlie_row[1] == "charlie@example.edu"
    assert charlie_row[2] == "CAMPUS-003"
    assert charlie_row[3] == "vip"
    assert charlie_row[5] == "Revoked"
    assert charlie_row[6] == ""
    assert charlie_row[7] == ""

    # - parse the summary rows at the end of the same CSV
    summary_rows = reader[blank_index + 1 :]
    assert summary_rows[0][0] == "Summary"
    summary_map = {row[0]: row[1] for row in summary_rows[1:] if len(row) >= 2}

    assert summary_map["Total Tickets Issued"] == "2"
    assert summary_map["Checked In"] == "1"
    assert summary_map["No-Shows"] == "1"
    assert summary_map["Revoked"] == "1"
    assert summary_map["Check-in Rate"] == "50%"


def test_export_attendance_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    # Unauthenticated call is rejected (401)
    unauth_response = client.get("/events/1/export")
    assert unauth_response.status_code == 401

    # Non-admin call is rejected (403)
    user_response = client.get("/events/1/export", headers=user_headers)
    assert user_response.status_code == 403

    scanner_response = client.get("/events/1/export", headers=scanner_headers)
    assert scanner_response.status_code == 403


def test_export_attendance_nonexistent_event_404() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    response = client.get("/events/999/export", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"





def test_migration_backfills_waitlist_links_on_a_legacy_database() -> None:
    """Production runs on a persistent SQLite volume, so migrate_existing_database()
    is the real upgrade path. The rest of the suite only ever calls
    drop_all/create_all, so nothing else covers it."""
    legacy_path = Path(tempfile.gettempdir()) / f"ticketx-legacy-{uuid4().hex}.db"
    legacy_url = f"sqlite:///{legacy_path.as_posix()}"
    legacy_engine = create_engine(legacy_url, connect_args={"check_same_thread": False})

    try:
        # Given: a database from before ET-07, where waitlist_entries carries only
        # denormalised attendee columns and no attendee_id / user_id links.
        with legacy_engine.begin() as setup:
            setup.execute(text("CREATE TABLE attendees (attendee_id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, campus_id VARCHAR(80) NOT NULL, contact_email VARCHAR(160) NOT NULL)"))
            setup.execute(text("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL, password_hash VARCHAR(160), role VARCHAR(24) NOT NULL, display_name VARCHAR(120) NOT NULL)"))
            setup.execute(text("CREATE TABLE events (event_id INTEGER PRIMARY KEY, title VARCHAR(160) NOT NULL, description VARCHAR(600) NOT NULL, date_time DATETIME NOT NULL, venue VARCHAR(160) NOT NULL, capacity INTEGER NOT NULL)"))
            setup.execute(text("CREATE TABLE tickets (ticket_id INTEGER PRIMARY KEY, qr_signature VARCHAR(180), tier VARCHAR(40) NOT NULL, seat_number VARCHAR(24), status VARCHAR(20) NOT NULL, issued_at DATETIME NOT NULL, revoked_at DATETIME, event_id INTEGER NOT NULL, attendee_id INTEGER NOT NULL)"))
            setup.execute(
                text(
                    "CREATE TABLE waitlist_entries (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, "
                    "attendee_name VARCHAR(120) NOT NULL, attendee_contact VARCHAR(160) NOT NULL, "
                    "campus_id VARCHAR(80), tier VARCHAR(40) NOT NULL, position INTEGER NOT NULL, "
                    "status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL)"
                )
            )
            setup.execute(text("INSERT INTO events (event_id, title, description, date_time, venue, capacity) VALUES (1, 'Legacy Fest', '', '2026-10-01T10:00:00', 'Hall A', 1)"))
            setup.execute(text("INSERT INTO users (user_id, username, password_hash, role, display_name) VALUES (1, 'attendee', 'x', 'user', 'Attendee')"))
            # One entry matching an existing Attendee, one needing a new Attendee row,
            # and one promoted row that must keep its place in history.
            setup.execute(text("INSERT INTO attendees (attendee_id, name, campus_id, contact_email) VALUES (1, 'Riya Sen', 'CAMP-1', 'riya@example.edu')"))
            setup.execute(
                text(
                    "INSERT INTO waitlist_entries (id, event_id, attendee_name, attendee_contact, campus_id, tier, position, status, created_at) "
                    "VALUES (1, 1, 'Riya Sen', 'riya@example.edu', 'CAMP-1', 'general', 1, 'waiting', '2026-09-01 10:00:00')"
                )
            )
            setup.execute(
                text(
                    "INSERT INTO waitlist_entries (id, event_id, attendee_name, attendee_contact, campus_id, tier, position, status, created_at) "
                    "VALUES (2, 1, 'Newcomer', 'newcomer@example.edu', NULL, 'premium', 2, 'waiting', '2026-09-01 11:00:00')"
                )
            )
            setup.execute(
                text(
                    "INSERT INTO waitlist_entries (id, event_id, attendee_name, attendee_contact, campus_id, tier, position, status, created_at) "
                    "VALUES (3, 1, 'Old Winner', 'old@example.edu', 'CAMP-OLD', 'general', 1, 'promoted', '2026-08-01 09:00:00')"
                )
            )

        # When: the migration runs
        with legacy_engine.begin() as setup:
            setup.execute(text("DROP INDEX IF EXISTS ix_waitlist_event_status_position"))
        original_database_url = database_module.DATABASE_URL
        original_engine = database_module.engine
        database_module.DATABASE_URL = legacy_url
        database_module.engine = legacy_engine
        try:
            database_module.migrate_existing_database()
        finally:
            database_module.DATABASE_URL = original_database_url
            database_module.engine = original_engine

        # Then: the new columns exist and pre-existing rows are preserved
        inspector = inspect(legacy_engine)
        columns = {column["name"] for column in inspector.get_columns("waitlist_entries")}
        assert {"attendee_id", "user_id", "promoted_ticket_id", "resolved_at"} <= columns
        assert {index["name"] for index in inspector.get_indexes("waitlist_entries")} >= {
            "ix_waitlist_event_status_position",
            "ix_waitlist_entries_attendee_id",
            "ix_waitlist_entries_user_id",
        }

        with legacy_engine.begin() as connection:
            rows = connection.execute(
                text("SELECT id, attendee_id, attendee_contact, position, status FROM waitlist_entries ORDER BY id")
            ).mappings().all()
            assert [row["id"] for row in rows] == [1, 2, 3]
            assert [row["position"] for row in rows] == [1, 2, 1]
            assert rows[2]["status"] == "promoted"

            # The pre-existing attendee is reused, not duplicated.
            assert rows[0]["attendee_id"] == 1
            # A campus-less entry is matched on its lowercased contact.
            assert rows[1]["attendee_id"] is not None
            # And a new Attendee row was created to back that link.
            new_attendee = connection.execute(
                text("SELECT campus_id, contact_email FROM attendees WHERE attendee_id = :aid"),
                {"aid": rows[1]["attendee_id"]},
            ).mappings().one()
            assert new_attendee["campus_id"] == "newcomer@example.edu"
            assert new_attendee["contact_email"] == "newcomer@example.edu"
            # No duplicate Attendee was created for the already-known campus id.
            assert connection.execute(
                text("SELECT COUNT(*) FROM attendees WHERE campus_id = 'CAMP-1'")
            ).scalar() == 1

        # And: re-running the migration is a no-op rather than duplicating rows
        database_module.DATABASE_URL = legacy_url
        database_module.engine = legacy_engine
        try:
            database_module.migrate_existing_database()
        finally:
            database_module.DATABASE_URL = original_database_url
            database_module.engine = original_engine
        with legacy_engine.begin() as connection:
            # Three identities: CAMP-1 pre-existing, the campus-less contact, and CAMP-OLD.
            assert connection.execute(text("SELECT COUNT(*) FROM attendees")).scalar() == 3
            assert connection.execute(text("SELECT COUNT(*) FROM waitlist_entries")).scalar() == 3
            # Every entry ended up linked, including the already-promoted one.
            assert connection.execute(
                text("SELECT COUNT(*) FROM waitlist_entries WHERE attendee_id IS NULL")
            ).scalar() == 0
    finally:
        legacy_engine.dispose()
        legacy_path.unlink(missing_ok=True)

def _capacity_one_event(client: TestClient, admin_headers: dict[str, str], title: str) -> int:
    return client.post(
        "/events",
        json={
            "title": title,
            "description": "",
            "date_time": "2026-11-20T18:00:00",
            "venue": "Main Auditorium",
            "capacity": 1,
        },
        headers=admin_headers,
    ).json()["id"]


def test_repeat_waitlist_join_is_idempotent() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Idempotent Waitlist")

    client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    )

    payload = {
        "event_id": event_id,
        "attendee_name": "Impatient Attendee",
        "attendee_contact": "impatient@example.edu",
        "campus_id": "CAMP-IDEM",
        "tier": "general",
    }
    first = client.post("/tickets", json=payload, headers=user_headers).json()
    # A retry of the same submission must not hand out a second queue place.
    second = client.post("/tickets", json=payload, headers=user_headers).json()
    third = client.post("/tickets", json=payload, headers=user_headers).json()

    assert first["position"] == 1
    assert second["position"] == 1
    assert third["position"] == 1
    assert first["waitlist_entry_id"] == second["waitlist_entry_id"] == third["waitlist_entry_id"]

    with SessionLocal() as db:
        entries = db.query(WaitlistEntry).filter(WaitlistEntry.event_id == event_id).all()
        assert len(entries) == 1
        assert entries[0].attendee_id is not None
        assert entries[0].user_id is not None


def test_promoted_attendee_recovers_their_ticket_from_me_registrations() -> None:
    """ET-07 acceptance: a seat opening up is automatically issued to the next
    waitlisted attendee, and that attendee can actually collect it."""
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Promotion Notice")

    holder = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    ).json()
    assert holder["outcome"] == "ticketed"

    client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Waiting Person",
            "attendee_contact": "waiting@example.edu",
            "campus_id": "CAMP-WAIT",
            "tier": "premium",
        },
        headers=user_headers,
    )

    # Before any revocation the attendee sees a live position.
    before = client.get("/me/registrations", headers=user_headers)
    assert before.status_code == 200
    entry = before.json()["waitlist"][0]
    assert entry["status"] == "waiting"
    assert entry["position"] == 1
    assert entry["event_title"] == "Promotion Notice"
    assert entry["promoted_ticket"] is None

    # When: ET-11 revokes the seat
    revoked = client.post(f"/tickets/{holder['ticket_id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["promoted_attendee"]["attendee_name"] == "Waiting Person"

    # Then: the same caller's registrations now carry the issued ticket, QR and all
    after = client.get("/me/registrations", headers=user_headers).json()["waitlist"][0]
    assert after["status"] == "promoted"
    assert after["position"] is None
    assert after["promoted_ticket"] is not None
    ticket = after["promoted_ticket"]
    assert ticket["status"] == "issued"
    assert ticket["tier"] == "premium"
    assert ticket["qr_signature"].startswith("TX-")
    assert ticket["seat_number"]
    # The signature is real, so the promoted ticket actually scans.
    assert ticket_id_from_signature(ticket["qr_signature"]) == ticket["ticket_id"]
    assert ticket["event"]["id"] == event_id

    # And: a reload of the ticket endpoint agrees
    reloaded = client.get(f"/tickets/{ticket['ticket_id']}", headers=user_headers)
    assert reloaded.status_code == 200
    assert reloaded.json()["qr_signature"] == ticket["qr_signature"]


def test_leaving_the_waitlist_compacts_positions() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Withdrawal")

    client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    )
    first = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Leaving Early",
            "attendee_contact": "leaving@example.edu",
            "campus_id": "CAMP-LEAVE",
        },
        headers=user_headers,
    ).json()
    client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Stays Waiting",
            "attendee_contact": "stays@example.edu",
            "campus_id": "CAMP-STAY",
        },
        headers=user_headers,
    )

    assert first["position"] == 1
    second_entry = client.get("/me/registrations", headers=user_headers).json()["waitlist"]
    assert [row["position"] for row in second_entry] == [2, 1]

    # When: the person at #1 withdraws
    withdrawn = client.delete(f"/me/waitlist/{first['waitlist_entry_id']}", headers=user_headers)
    assert withdrawn.status_code == 204

    # Then: the remaining entry slides up to #1 and is marked cancelled, not deleted
    rows = client.get("/me/registrations", headers=user_headers).json()["waitlist"]
    withdrawn_row = next(row for row in rows if row["waitlist_entry_id"] == first["waitlist_entry_id"])
    assert withdrawn_row["status"] == "cancelled"
    assert withdrawn_row["position"] is None
    still_waiting = next(row for row in rows if row["attendee_name"] == "Stays Waiting")
    assert still_waiting["status"] == "waiting"
    assert still_waiting["position"] == 1

    with SessionLocal() as db:
        entries = db.query(WaitlistEntry).filter(WaitlistEntry.event_id == event_id).all()
        assert {entry.status for entry in entries} == {"cancelled", "waiting"}


def test_withdraw_cannot_touch_another_users_entry() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Ownership")

    client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    )
    entry = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Mine",
            "attendee_contact": "mine@example.edu",
            "campus_id": "CAMP-MINE",
        },
        headers=user_headers,
    ).json()

    # A scanner has no queue places to withdraw, so the role guard answers first.
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    assert client.delete(f"/me/waitlist/{entry['waitlist_entry_id']}", headers=scanner_headers).status_code == 403
    # An entry that does not exist is a 404, and does not leak whether it would.
    assert client.delete("/me/waitlist/999999", headers=user_headers).status_code == 404

    # Still the owner's to withdraw.
    assert client.delete(f"/me/waitlist/{entry['waitlist_entry_id']}", headers=user_headers).status_code == 204
    # And withdrawing again is a conflict rather than a silent success.
    assert client.delete(f"/me/waitlist/{entry['waitlist_entry_id']}", headers=user_headers).status_code == 409

    with SessionLocal() as db:
        row = db.get(WaitlistEntry, entry["waitlist_entry_id"])
        assert row is not None
        assert row.status == "cancelled"


def test_me_registrations_requires_a_token() -> None:
    reset_database()
    client = TestClient(app)

    assert client.get("/me/registrations").status_code == 401
