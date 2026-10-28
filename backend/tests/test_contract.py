import csv
import io
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app import database as database_module
from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import Attendee, Event, Scan, Seat, SeatHold, Ticket, User, Volunteer, WaitlistEntry
from backend.app.security import ticket_id_from_signature
from backend.app.seed import backfill_demo_metadata, seed_reference_data
from backend.app.services import release_expired_holds


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
            # A pre-ownership ticket: it must survive with user_id left NULL rather
            # than being attributed to whoever happens to hold the seat today.
            setup.execute(
                text(
                    "INSERT INTO tickets (ticket_id, qr_signature, tier, seat_number, status, issued_at, event_id, attendee_id) "
                    "VALUES (1, 'legacy-sig', 'general', NULL, 'issued', '2026-08-01 08:00:00', 1, 1)"
                )
            )
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

        # Tickets predate ownership, so user_id is added empty and indexed rather
        # than backfilled: the attendee is not the account that booked.
        ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
        assert "user_id" in ticket_columns
        assert {index["name"] for index in inspector.get_indexes("tickets")} >= {"ix_tickets_user_id"}
        with legacy_engine.begin() as connection:
            legacy_ticket = connection.execute(
                text("SELECT user_id, seat_number FROM tickets WHERE ticket_id = 1")
            ).mappings().one()
            assert legacy_ticket["user_id"] is None
            # The seat-number backfill still ran alongside the new column.
            assert legacy_ticket["seat_number"] == "GEN-001"

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


def test_migration_adds_seat_map_columns_to_a_legacy_database() -> None:
    """Part 3's upgrade path: a database created before seat maps must gain the
    event flag and the per-seat ticket link, twice over, without losing rows."""
    legacy_path = Path(tempfile.gettempdir()) / f"ticketx-seat-migration-{uuid4().hex}.db"
    legacy_url = f"sqlite:///{legacy_path.as_posix()}"
    legacy_engine = create_engine(legacy_url, connect_args={"check_same_thread": False})

    try:
        # Given: the pre-seat-map schema. No has_seat_map, no seat_id, and no
        # seats / seat_holds tables at all.
        with legacy_engine.begin() as setup:
            setup.execute(
                text(
                    "CREATE TABLE events (event_id INTEGER PRIMARY KEY, title VARCHAR(160) NOT NULL, "
                    "description VARCHAR(600) NOT NULL, date_time DATETIME NOT NULL, "
                    "venue VARCHAR(160) NOT NULL, capacity INTEGER NOT NULL)"
                )
            )
            setup.execute(
                text(
                    "CREATE TABLE attendees (attendee_id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, "
                    "campus_id VARCHAR(80) NOT NULL, contact_email VARCHAR(160) NOT NULL)"
                )
            )
            setup.execute(
                text(
                    "CREATE TABLE tickets (ticket_id INTEGER PRIMARY KEY, qr_signature VARCHAR(180), "
                    "tier VARCHAR(40) NOT NULL, seat_number VARCHAR(24), status VARCHAR(20) NOT NULL, "
                    "issued_at DATETIME NOT NULL, revoked_at DATETIME, event_id INTEGER NOT NULL, "
                    "attendee_id INTEGER NOT NULL)"
                )
            )
            setup.execute(
                text(
                    "INSERT INTO events (event_id, title, description, date_time, venue, capacity) "
                    "VALUES (1, 'Legacy Fest', '', '2026-10-01T10:00:00', 'Hall A', 500)"
                )
            )
            setup.execute(
                text(
                    "INSERT INTO attendees (attendee_id, name, campus_id, contact_email) "
                    "VALUES (1, 'Riya Sen', 'CAMP-1', 'riya@example.edu')"
                )
            )
            setup.execute(
                text(
                    "INSERT INTO tickets (ticket_id, qr_signature, tier, seat_number, status, issued_at, event_id, attendee_id) "
                    "VALUES (1, 'legacy-sig', 'general', 'GEN-001', 'issued', '2026-08-01 08:00:00', 1, 1)"
                )
            )

        original_database_url = database_module.DATABASE_URL
        original_engine = database_module.engine
        database_module.DATABASE_URL = legacy_url
        database_module.engine = legacy_engine
        try:
            database_module.migrate_existing_database()
            # When: it runs a second time. Idempotency is the requirement, not a
            # nice-to-have: every app start calls this.
            database_module.migrate_existing_database()
        finally:
            database_module.DATABASE_URL = original_database_url
            database_module.engine = original_engine

        # Then: the columns exist, and so do the tables the seat FK points at.
        inspector = inspect(legacy_engine)
        assert {"seats", "seat_holds"} <= set(inspector.get_table_names())

        event_columns = {column["name"] for column in inspector.get_columns("events")}
        ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
        assert "has_seat_map" in event_columns
        assert "seat_id" in ticket_columns

        seat_index = next(
            index for index in inspector.get_indexes("tickets") if index["name"] == "ix_tickets_seat_id"
        )
        assert seat_index["unique"] == 1
        assert seat_index["column_names"] == ["seat_id"]

        # And: the old rows survived with sensible values for the new columns.
        with legacy_engine.begin() as connection:
            event = connection.execute(
                text("SELECT title, capacity, has_seat_map FROM events WHERE event_id = 1")
            ).mappings().one()
            assert event["title"] == "Legacy Fest"
            assert event["capacity"] == 500
            assert event["has_seat_map"] == 0

            ticket = connection.execute(
                text("SELECT seat_number, seat_id, status FROM tickets WHERE ticket_id = 1")
            ).mappings().one()
            assert ticket["seat_number"] == "GEN-001"
            assert ticket["seat_id"] is None
            assert ticket["status"] == "issued"

            assert connection.execute(text("SELECT COUNT(*) FROM tickets")).scalar() == 1
            assert connection.execute(text("SELECT COUNT(*) FROM events")).scalar() == 1

        # The index is a real constraint, not just metadata: one ticket per seat.
        with legacy_engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO tickets (ticket_id, qr_signature, tier, seat_number, status, issued_at, event_id, attendee_id, seat_id) "
                    "VALUES (2, 'sig-2', 'general', 'A1', 'issued', '2026-08-02 08:00:00', 1, 1, 42)"
                )
            )
            connection.commit()
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO tickets (ticket_id, qr_signature, tier, seat_number, status, issued_at, event_id, attendee_id, seat_id) "
                        "VALUES (3, 'sig-3', 'general', 'A1', 'issued', '2026-08-03 08:00:00', 1, 1, 42)"
                    )
                )
            connection.rollback()
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

def test_admin_stats_confirms_a_promotion_reached_the_waitlist() -> None:
    """ET-11 hook, seen from the admin side: revoking a seat must be auditable
    as 'this person got it', not just as a counter moving."""
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Promotion Audit")

    holder = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    ).json()
    client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Next In Line",
            "attendee_contact": "next@example.edu",
            "campus_id": "CAMP-NEXT",
        },
        headers=user_headers,
    )

    before = client.get(f"/events/{event_id}/stats", headers=admin_headers).json()
    assert [row["position"] for row in before["waitlist"]] == [1]
    assert before["promoted"] == []

    client.post(f"/tickets/{holder['ticket_id']}/revoke", headers=admin_headers)

    after = client.get(f"/events/{event_id}/stats", headers=admin_headers).json()
    assert after["waitlist"] == []
    assert len(after["promoted"]) == 1
    promoted = after["promoted"][0]
    assert promoted["attendee_name"] == "Next In Line"
    assert promoted["status"] == "promoted"
    assert promoted["promoted_ticket_id"] is not None
    assert promoted["resolved_at"] is not None

    # The promoted ticket is counted as issued, and the revoked one is not.
    assert after["issued"] == 1
    assert {row["status"] for row in after["tickets"]} == {"issued", "revoked"}


def test_withdrawn_entry_is_absent_from_the_admin_waitlist() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Withdrawal Audit")

    client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    )
    entry = client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": "Gives Up",
            "attendee_contact": "givesup@example.edu",
            "campus_id": "CAMP-GIVEUP",
        },
        headers=user_headers,
    ).json()

    client.delete(f"/me/waitlist/{entry['waitlist_entry_id']}", headers=user_headers)

    stats = client.get(f"/events/{event_id}/stats", headers=admin_headers).json()
    assert stats["waitlist"] == []
    # A withdrawal is neither a promotion nor a live place in the queue.
    assert stats["promoted"] == []


def test_registering_twice_for_one_event_is_rejected() -> None:
    """A double click or a retry must not hand the same attendee a second seat.

    The waitlist join has always been idempotent; the ticketed path was not, so
    a duplicate submit silently consumed capacity that someone in the queue
    needed.
    """
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = client.post(
        "/events",
        json={
            "title": "Duplicate Guard",
            "description": "",
            "date_time": "2026-12-01T18:00:00",
            "venue": "Main Auditorium",
            "capacity": 10,
        },
        headers=admin_headers,
    ).json()["id"]

    payload = {
        "event_id": event_id,
        "attendee_name": "Riya Sen",
        "attendee_contact": "riya@example.edu",
        "campus_id": "CAMP-DUP",
        "tier": "general",
    }
    first = client.post("/tickets", json=payload, headers=user_headers)
    second = client.post("/tickets", json=payload, headers=user_headers)

    assert first.status_code == 201
    assert first.json()["outcome"] == "ticketed"
    assert second.status_code == 409
    assert "already hold a ticket" in second.json()["detail"]

    # One seat issued, and the event's capacity is untouched by the retry.
    event = client.get("/events").json()
    assert next(item for item in event if item["id"] == event_id)["issued_count"] == 1

    # The block is scoped to the person, not the account: one account can still
    # book a different attendee for the same event.
    companion = client.post(
        "/tickets",
        json={**payload, "attendee_name": "Companion", "attendee_contact": "companion@example.edu", "campus_id": "CAMP-2"},
        headers=user_headers,
    )
    assert companion.status_code == 201


def test_a_revoked_ticket_can_be_registered_for_again() -> None:
    """Revocation hands the seat to the waitlist, but the person may rebook if
    it is still their turn to want it."""
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Rebook After Revoke")

    issued = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu", "campus_id": "CAMP-RB"},
        headers=user_headers,
    ).json()
    blocked = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu", "campus_id": "CAMP-RB"},
        headers=user_headers,
    )
    assert blocked.status_code == 409

    client.post(f"/tickets/{issued['ticket_id']}/revoke", headers=admin_headers)

    # The seat is free and the old ticket no longer counts as held, so the same
    # account and attendee can book it again.
    rebooked = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu", "campus_id": "CAMP-RB"},
        headers=user_headers,
    )
    assert rebooked.status_code == 201
    assert rebooked.json()["outcome"] == "ticketed"


def test_me_registrations_lists_the_callers_own_tickets() -> None:
    """The list behind "my tickets", so a ticket survives a reload."""
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other = client.post(
        "/auth/register",
        json={"username": "second", "password": "second123", "display_name": "Second User", "role": "user"},
    ).json()
    other_headers = {"Authorization": f"Bearer {other['token']}"}

    fest = client.get("/events").json()[0]["id"]
    other_event = client.post(
        "/events",
        json={
            "title": "Second Event",
            "description": "",
            "date_time": "2026-12-05T18:00:00",
            "venue": "Hall B",
            "capacity": 50,
        },
        headers=admin_headers,
    ).json()["id"]

    mine = client.post(
        "/tickets",
        json={"event_id": fest, "attendee_name": "Student User", "attendee_contact": "me@example.edu", "campus_id": "CAMP-ME", "tier": "premium"},
        headers=user_headers,
    ).json()
    theirs = client.post(
        "/tickets",
        json={"event_id": other_event, "attendee_name": "Second User", "attendee_contact": "them@example.edu", "campus_id": "CAMP-THEM"},
        headers=other_headers,
    ).json()

    body = client.get("/me/registrations", headers=user_headers).json()
    assert [item["ticket_id"] for item in body["tickets"]] == [mine["ticket_id"]]
    # The full pass is embedded, so the QR renders without a second fetch.
    ticket = body["tickets"][0]
    assert ticket["tier"] == "premium"
    assert ticket["seat_number"] == "PRE-001"
    assert ticket["status"] == "issued"
    assert ticket["qr_signature"]
    assert ticket["event"]["title"] == "Techno Cultural Fest 2026"
    assert ticket["attendee"]["campus_id"] == "CAMP-ME"

    # Another account never sees it, and never sees their own in this list.
    assert theirs["ticket_id"] != mine["ticket_id"]
    other_body = client.get("/me/registrations", headers=other_headers).json()
    assert [item["ticket_id"] for item in other_body["tickets"]] == [theirs["ticket_id"]]

    # A revoked ticket drops out: it no longer holds a seat.
    client.post(f"/tickets/{mine['ticket_id']}/revoke", headers=admin_headers)
    after = client.get("/me/registrations", headers=user_headers).json()
    assert after["tickets"] == []
    assert after["waitlist"] == []


def test_promoted_ticket_also_appears_in_my_tickets() -> None:
    """A seat freed by a revocation reaches the waitlist, and the ticket it
    issues belongs to the same account that asked for the place."""
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event_id = _capacity_one_event(client, admin_headers, "Promotion Ownership")

    holder = client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Seat Holder", "attendee_contact": "holder@example.edu"},
        headers=user_headers,
    ).json()
    client.post(
        "/tickets",
        json={"event_id": event_id, "attendee_name": "Next In Line", "attendee_contact": "next@example.edu", "campus_id": "CAMP-NEXT"},
        headers=user_headers,
    )

    client.post(f"/tickets/{holder['ticket_id']}/revoke", headers=admin_headers)

    body = client.get("/me/registrations", headers=user_headers).json()
    assert [item["event"]["id"] for item in body["tickets"]] == [event_id]
    assert body["tickets"][0]["attendee"]["campus_id"] == "CAMP-NEXT"
    # The same ticket is also reachable through the promoted queue entry.
    assert [entry["promoted_ticket"]["ticket_id"] for entry in body["waitlist"]] == [
        body["tickets"][0]["ticket_id"]
    ]


def _seat_map_payload(tiers: list[dict], title: str = "Seat Map Event") -> dict:
    return {
        "title": title,
        "description": "",
        "date_time": "2026-12-12T18:00:00",
        "venue": "Main Auditorium",
        "seat_layout": {"tiers": tiers},
    }


def _create_seat_map_event(
    client: TestClient,
    admin_headers: dict[str, str],
    tiers: list[dict],
    title: str = "Seat Map Event",
) -> dict:
    response = client.post("/events", json=_seat_map_payload(tiers, title), headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


def _flatten_seat_map(body: dict) -> list[dict]:
    return [
        seat
        for tier in body["tiers"]
        for row in tier["rows"]
        for block in row["blocks"]
        for seat in block
    ]


def test_seat_layout_validation_failures_return_422() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    invalid_layouts = [
        # At least one tier.
        {"tiers": []},
        # Tier names are non-empty and unique case-insensitively.
        {"tiers": [{"name": "   ", "rows": [{"label": "A", "blocks": [1]}]}]},
        {
            "tiers": [
                {"name": "Premium", "rows": [{"label": "A", "blocks": [1]}]},
                {"name": "premium", "rows": [{"label": "B", "blocks": [1]}]},
            ]
        },
        # Every tier needs a row and every row needs a block.
        {"tiers": [{"name": "Premium", "rows": []}]},
        {"tiers": [{"name": "Premium", "rows": [{"label": "A", "blocks": []}]}]},
        # Row labels are case-insensitively unique, alphanumeric, and 1-24 chars.
        {"tiers": [{"name": "Premium", "rows": [{"label": "A", "blocks": [1]}, {"label": "a", "blocks": [1]}]}]},
        {"tiers": [{"name": "Premium", "rows": [{"label": "A-1", "blocks": [1]}]}]},
        {"tiers": [{"name": "Premium", "rows": [{"label": "A", "blocks": [0]}]}]},
        # Blocked numbers must be inside the row's 1..sum(blocks) range.
        {"tiers": [{"name": "Premium", "rows": [{"label": "A", "blocks": [3], "blocked": [4]}]}]},
        # More than 10 tiers.
        {
            "tiers": [
                {"name": f"Tier {index}", "rows": [{"label": f"R{index}", "blocks": [1]}]}
                for index in range(1, 12)
            ]
        },
        # More than 60 rows.
        {
            "tiers": [
                {
                    "name": "Premium",
                    "rows": [{"label": f"R{index}", "blocks": [1]} for index in range(1, 62)],
                }
            ]
        },
        # More than 2000 seats, including blocked ones.
        {"tiers": [{"name": "Premium", "rows": [{"label": "A", "blocks": [2001]}]}]},
        # Label = row label + seat number, so these two rows both generate A12.
        {
            "tiers": [
                {
                    "name": "Premium",
                    "rows": [
                        {"label": "A1", "blocks": [2]},
                        {"label": "A", "blocks": [12]},
                    ],
                }
            ]
        },
    ]

    for layout in invalid_layouts:
        response = client.post("/events", json=_seat_map_payload(layout["tiers"]), headers=admin_headers)
        assert response.status_code == 422, (layout, response.text)

    # None of the invalid layouts left a half-created event behind.
    assert len(client.get("/events").json()) == 1


def test_seat_generation_numbers_across_blocks() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [3, 4, 3], "blocked": [2]}]}],
    )

    assert event["has_seat_map"] is True
    assert event["capacity"] == 9
    assert event["tiers"] == [{"name": "Premium", "total": 9, "available": 9}]

    with SessionLocal() as db:
        seats = (
            db.query(Seat)
            .filter(Seat.event_id == event["id"])
            .order_by(Seat.seat_number.asc())
            .all()
        )
        assert len(seats) == 10
        assert [seat.seat_number for seat in seats] == list(range(1, 11))
        assert [seat.label for seat in seats] == [f"A{number}" for number in range(1, 11)]
        assert [seat.block_index for seat in seats] == [0, 0, 0, 1, 1, 1, 1, 2, 2, 2]
        assert sum(1 for seat in seats if seat.is_blocked) == 1
        assert seats[1].label == "A2"
        assert seats[1].is_blocked is True

    body = client.get(f"/events/{event['id']}/seats", headers=user_headers)
    assert body.status_code == 200
    seat_map = body.json()
    row_a = seat_map["tiers"][0]["rows"][0]
    assert [len(block) for block in row_a["blocks"]] == [3, 4, 3]
    assert [[seat["number"] for seat in block] for block in row_a["blocks"]] == [
        [1, 2, 3],
        [4, 5, 6, 7],
        [8, 9, 10],
    ]
    assert row_a["blocks"][0][1]["label"] == "A2"
    assert row_a["blocks"][0][1]["state"] == "blocked"
    assert seat_map["total"] == 9
    assert seat_map["available"] == 9
    assert seat_map["held"] == 0
    assert seat_map["booked"] == 0


def test_seat_map_structure_tiers_and_event_listing_counts() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    tiers = [
        {
            "name": "Premium",
            "rows": [
                {"label": "A", "blocks": [3, 4, 3], "blocked": [2]},
                {"label": "B", "blocks": [2]},
            ],
        },
        {"name": "General", "rows": [{"label": "C", "blocks": [4]}]},
    ]
    event = _create_seat_map_event(client, admin_headers, tiers)

    body = client.get(f"/events/{event['id']}/seats", headers=user_headers)
    assert body.status_code == 200
    seat_map = body.json()

    assert [tier["name"] for tier in seat_map["tiers"]] == ["Premium", "General"]
    assert [row["label"] for row in seat_map["tiers"][0]["rows"]] == ["A", "B"]
    assert [len(block) for block in seat_map["tiers"][0]["rows"][0]["blocks"]] == [3, 4, 3]
    assert [seat["label"] for seat in _flatten_seat_map(seat_map)] == [
        "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10",
        "B1", "B2", "C1", "C2", "C3", "C4",
    ]
    assert seat_map["total"] == 15
    assert seat_map["available"] == 15
    assert seat_map["held"] == 0
    assert seat_map["booked"] == 0

    listed = next(item for item in client.get("/events").json() if item["id"] == event["id"])
    assert listed["has_seat_map"] is True
    assert listed["capacity"] == 15
    assert listed["tiers"] == [
        {"name": "Premium", "total": 11, "available": 11},
        {"name": "General", "total": 4, "available": 4},
    ]

    # The seeded event has no layout, so it keeps the legacy shape.
    legacy = next(item for item in client.get("/events").json() if item["id"] == 1)
    assert legacy["has_seat_map"] is False
    assert legacy["tiers"] == []


def test_seat_map_errors_and_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )

    assert client.get(f"/events/{event['id']}/seats").status_code == 401
    assert client.get(f"/events/{event['id']}/seats", headers=scanner_headers).status_code == 200
    assert client.get(f"/events/{event['id']}/seats", headers=user_headers).status_code == 200

    legacy = client.get("/events/1/seats", headers=user_headers)
    assert legacy.status_code == 409
    assert legacy.json()["detail"] == "this event has no seat map"

    missing = client.get("/events/999/seats", headers=user_headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Event not found"


def test_seat_map_states_booked_held_and_mine() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [4]}]}],
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "attendee").one()
        seats = (
            db.query(Seat)
            .filter(Seat.event_id == event["id"])
            .order_by(Seat.seat_number.asc())
            .all()
        )
        hold = SeatHold(
            event_id=event["id"],
            user_id=user.id,
            status="active",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(hold)
        db.flush()
        seats[0].hold_id = hold.id
        seats[1].hold_id = hold.id

        attendee = Attendee(name="Booked Person", campus_id="CAMP-BOOKED", contact_email="booked@example.edu")
        db.add(attendee)
        db.flush()
        db.add(
            Ticket(
                event_id=event["id"],
                attendee_id=attendee.id,
                tier="general",
                seat_number=seats[2].label,
                seat_id=seats[2].id,
                status="issued",
                user_id=user.id,
            )
        )
        db.commit()

    body = client.get(f"/events/{event['id']}/seats", headers=user_headers)
    assert body.status_code == 200
    seat_map = body.json()
    seats_out = _flatten_seat_map(seat_map)

    assert [seat["state"] for seat in seats_out] == ["held", "held", "booked", "available"]
    # "mine" only describes an active hold, not a ticket the caller booked.
    assert [seat["mine"] for seat in seats_out] == [True, True, False, False]
    assert seat_map["total"] == 4
    assert seat_map["available"] == 1
    assert seat_map["held"] == 2
    assert seat_map["booked"] == 1

    listed = next(item for item in client.get("/events").json() if item["id"] == event["id"])
    assert listed["tiers"] == [{"name": "Premium", "total": 4, "available": 1}]


def test_release_expired_holds_frees_seats() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "attendee").one()
        seat = (
            db.query(Seat)
            .filter(Seat.event_id == event["id"])
            .order_by(Seat.seat_number.asc())
            .first()
        )
        hold = SeatHold(
            event_id=event["id"],
            user_id=user.id,
            status="active",
            created_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=10),
        )
        db.add(hold)
        db.flush()
        seat.hold_id = hold.id
        db.commit()
        hold_id = hold.id
        seat_id = seat.id

    with SessionLocal() as db:
        assert release_expired_holds(db, event["id"]) == 1
        assert db.get(SeatHold, hold_id).status == "expired"
        assert db.get(Seat, seat_id).hold_id is None

    body = client.get(f"/events/{event['id']}/seats", headers=user_headers)
    assert body.status_code == 200
    assert [seat["state"] for seat in _flatten_seat_map(body.json())] == ["available", "available"]


def test_deleting_a_seat_map_event_removes_seats_and_holds() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "attendee").one()
        hold = SeatHold(
            event_id=event["id"],
            user_id=user.id,
            status="active",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(hold)
        db.commit()

    deleted = client.delete(f"/events/{event['id']}", headers=admin_headers)
    assert deleted.status_code == 204

    with SessionLocal() as db:
        assert db.query(Seat).filter(Seat.event_id == event["id"]).count() == 0
        assert db.query(SeatHold).filter(SeatHold.event_id == event["id"]).count() == 0


def test_legacy_event_and_legacy_ticket_flow_are_unchanged() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    event = client.post(
        "/events",
        json={
            "title": "Legacy Event",
            "description": "",
            "date_time": "2026-12-20T18:00:00",
            "venue": "Hall A",
            "capacity": 2,
        },
        headers=admin_headers,
    )
    assert event.status_code == 201
    created = event.json()
    assert created["has_seat_map"] is False
    assert created["tiers"] == []
    assert created["capacity"] == 2

    ticket = client.post(
        "/tickets",
        json={
            "event_id": created["id"],
            "attendee_name": "Legacy Attendee",
            "attendee_contact": "legacy@example.edu",
            "tier": "general",
        },
        headers=user_headers,
    )
    assert ticket.status_code == 201
    assert ticket.json()["seat_number"] == "GEN-001"

    with SessionLocal() as db:
        assert db.query(Seat).filter(Seat.event_id == created["id"]).count() == 0


# ---------------------------------------------------------------------------
# Part 2: seat holds, per-seat booking, and the seat-aware waitlist
# ---------------------------------------------------------------------------


def _register_user(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": "secret123",
            "display_name": username.title(),
            "role": "user",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _seat_ids(client: TestClient, event_id: int, headers: dict[str, str]) -> list[int]:
    body = client.get(f"/events/{event_id}/seats", headers=headers)
    assert body.status_code == 200, body.text
    return [seat["id"] for seat in _flatten_seat_map(body.json())]


def _seat_map(client: TestClient, event_id: int, headers: dict[str, str]) -> dict[str, dict]:
    body = client.get(f"/events/{event_id}/seats", headers=headers)
    assert body.status_code == 200, body.text
    return {seat["label"]: seat for seat in _flatten_seat_map(body.json())}


def _hold(
    client: TestClient,
    headers: dict[str, str],
    event_id: int,
    seat_ids: list[int],
):
    return client.post(f"/events/{event_id}/holds", json={"seat_ids": seat_ids}, headers=headers)


def _confirm(
    client: TestClient,
    headers: dict[str, str],
    hold_id: int,
    name: str = "Seat Holder",
    contact: str = "holder@example.edu",
    campus_id: str | None = "CAMP-HOLD",
):
    return client.post(
        f"/holds/{hold_id}/confirm",
        json={"attendee_name": name, "attendee_contact": contact, "campus_id": campus_id},
        headers=headers,
    )


def _book(
    client: TestClient,
    headers: dict[str, str],
    event_id: int,
    tier: str,
    name: str,
    contact: str,
):
    return client.post(
        "/tickets",
        json={
            "event_id": event_id,
            "attendee_name": name,
            "attendee_contact": contact,
            "tier": tier,
        },
        headers=headers,
    )


def test_hold_marks_seats_held_for_owner_and_others() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other_headers = _register_user(client, "second")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [3]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)

    response = _hold(client, user_headers, event["id"], ids[:2])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["event_id"] == event["id"]
    assert 0 < body["seconds_remaining"] <= 600
    assert [seat["label"] for seat in body["seats"]] == ["A1", "A2"]

    fetched = client.get(f"/holds/{body['hold_id']}", headers=user_headers)
    assert fetched.status_code == 200
    assert [seat["label"] for seat in fetched.json()["seats"]] == ["A1", "A2"]

    mine = _seat_map(client, event["id"], user_headers)
    assert mine["A1"]["state"] == "held"
    assert mine["A1"]["mine"] is True
    assert mine["A3"]["state"] == "available"
    assert mine["A3"]["mine"] is False

    theirs = _seat_map(client, event["id"], other_headers)
    assert theirs["A1"]["state"] == "held"
    assert theirs["A1"]["mine"] is False


def test_hold_is_all_or_nothing() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other_headers = _register_user(client, "second")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [6]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)

    assert _hold(client, other_headers, event["id"], [ids[2]]).status_code == 201

    response = _hold(client, user_headers, event["id"], [ids[1], ids[2], ids[3]])
    assert response.status_code == 409
    assert response.json()["detail"] == "One or more seats are no longer available"

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A2"]["state"] == "available"
    assert seats["A3"]["state"] == "held"
    assert seats["A4"]["state"] == "available"


def test_failed_hold_keeps_previous_hold_intact() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other_headers = _register_user(client, "second")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [5]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)

    first = _hold(client, user_headers, event["id"], [ids[0], ids[1]])
    assert first.status_code == 201
    hold_id = first.json()["hold_id"]
    assert _hold(client, other_headers, event["id"], [ids[3]]).status_code == 201

    # Claims A3 and A4; A4 is taken, so the whole attempt rolls back.
    assert _hold(client, user_headers, event["id"], [ids[2], ids[3]]).status_code == 409

    kept = client.get(f"/holds/{hold_id}", headers=user_headers)
    assert kept.status_code == 200
    assert kept.json()["status"] == "active"
    assert [seat["label"] for seat in kept.json()["seats"]] == ["A1", "A2"]

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A1"]["state"] == "held"
    assert seats["A2"]["state"] == "held"
    assert seats["A3"]["state"] == "available"


def test_two_users_cannot_hold_the_same_seat() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other_headers = _register_user(client, "second")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)

    assert _hold(client, user_headers, event["id"], [ids[0]]).status_code == 201
    second = _hold(client, other_headers, event["id"], [ids[0]])
    assert second.status_code == 409
    assert second.json()["detail"] == "One or more seats are no longer available"

    # The same guarantee at the storage layer: one ticket per seat.
    with SessionLocal() as db:
        seat = db.query(Seat).filter(Seat.id == ids[0]).one()
        attendee = Attendee(
            name="Duplicate Seat",
            campus_id="CAMP-DUPLICATE",
            contact_email="duplicate@example.edu",
        )
        db.add(attendee)
        db.flush()
        db.add(
            Ticket(
                event_id=event["id"],
                attendee_id=attendee.id,
                tier="Premium",
                seat_number="A1",
                seat_id=seat.id,
                status="issued",
            )
        )
        db.commit()

        db.add(
            Ticket(
                event_id=event["id"],
                attendee_id=attendee.id,
                tier="Premium",
                seat_number="A1",
                seat_id=seat.id,
                status="issued",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_hold_validations_and_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [
            {"name": "Premium", "rows": [{"label": "A", "blocks": [10], "blocked": [4]}]},
        ],
    )
    other_event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "Z", "blocks": [2]}]}],
        title="Other Seat Map Event",
    )
    ids = _seat_ids(client, event["id"], user_headers)
    other_ids = _seat_ids(client, other_event["id"], user_headers)
    seats_by_label = _seat_map(client, event["id"], user_headers)
    blocked_id = seats_by_label["A4"]["id"]
    open_ids = [seat["id"] for label, seat in seats_by_label.items() if label != "A4"]

    too_many = _hold(client, user_headers, event["id"], ids[:7])
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "select between 1 and 6 seats"

    empty = _hold(client, user_headers, event["id"], [])
    assert empty.status_code == 400

    duplicate = _hold(client, user_headers, event["id"], [ids[0], ids[0]])
    assert duplicate.status_code == 400

    foreign = _hold(client, user_headers, event["id"], [other_ids[0]])
    assert foreign.status_code == 400

    blocked = _hold(client, user_headers, event["id"], [blocked_id])
    assert blocked.status_code == 409

    legacy = _hold(client, user_headers, 1, [ids[0]])
    assert legacy.status_code == 409
    assert legacy.json()["detail"] == "this event has no seat map"

    missing = _hold(client, user_headers, 999, [ids[0]])
    assert missing.status_code == 404

    assert _hold(client, admin_headers, event["id"], [ids[0]]).status_code == 403
    assert _hold(client, scanner_headers, event["id"], [ids[0]]).status_code == 403
    assert client.post(f"/events/{event['id']}/holds", json={"seat_ids": [ids[0]]}).status_code == 401

    # Six is allowed: the cap is inclusive.
    assert _hold(client, user_headers, event["id"], open_ids[:6]).status_code == 201


def test_hold_ownership_is_reported_as_missing() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    other_headers = _register_user(client, "second")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)
    hold = _hold(client, user_headers, event["id"], [ids[0]]).json()

    assert client.get(f"/holds/{hold['hold_id']}", headers=other_headers).status_code == 404
    assert client.delete(f"/holds/{hold['hold_id']}", headers=other_headers).status_code == 404
    assert _confirm(client, other_headers, hold["hold_id"]).status_code == 404
    assert client.get("/holds/999", headers=user_headers).status_code == 404

    # The owner's hold survived the attempts.
    assert client.get(f"/holds/{hold['hold_id']}", headers=user_headers).json()["status"] == "active"


def test_expired_hold_releases_seats_and_refuses_confirm() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)
    hold = _hold(client, user_headers, event["id"], ids).json()

    with SessionLocal() as db:
        db.query(SeatHold).filter(SeatHold.id == hold["hold_id"]).update(
            {SeatHold.expires_at: datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)}
        )
        db.commit()

    assert all(seat["state"] == "available" for seat in _seat_map(client, event["id"], user_headers).values())

    fetched = client.get(f"/holds/{hold['hold_id']}", headers=user_headers)
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "expired"
    assert fetched.json()["seconds_remaining"] == 0
    assert fetched.json()["seats"] == []

    confirmed = _confirm(client, user_headers, hold["hold_id"])
    assert confirmed.status_code == 409
    assert confirmed.json()["detail"] == "hold expired"

    with SessionLocal() as db:
        assert db.get(SeatHold, hold["hold_id"]).status == "expired"
        assert db.query(Ticket).filter(Ticket.event_id == event["id"]).count() == 0


def test_new_hold_releases_the_previous_one() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [3]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)

    first = _hold(client, user_headers, event["id"], [ids[0]]).json()
    second = _hold(client, user_headers, event["id"], [ids[1]])
    assert second.status_code == 201

    previous = client.get(f"/holds/{first['hold_id']}", headers=user_headers).json()
    assert previous["status"] == "cancelled"
    assert previous["seats"] == []

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A1"]["state"] == "available"
    assert seats["A2"]["state"] == "held"


def test_confirm_issues_one_ticket_per_seat() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    user_id = client.get("/auth/me", headers=user_headers).json()["id"]
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Balcony", "rows": [{"label": "A", "blocks": [3]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)
    hold = _hold(client, user_headers, event["id"], ids[:2]).json()

    response = _confirm(client, user_headers, hold["hold_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert [ticket["seat_number"] for ticket in body["tickets"]] == ["A1", "A2"]
    assert {ticket["tier"] for ticket in body["tickets"]} == {"Balcony"}
    assert {ticket["attendee"]["campus_id"] for ticket in body["tickets"]} == {"CAMP-HOLD"}
    assert {ticket["attendee"]["name"] for ticket in body["tickets"]} == {"Seat Holder"}
    assert [ticket_id_from_signature(ticket["qr_signature"]) for ticket in body["tickets"]] == [
        ticket["ticket_id"] for ticket in body["tickets"]
    ]
    ticket_ids = {ticket["ticket_id"] for ticket in body["tickets"]}

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A1"]["state"] == "booked"
    assert seats["A2"]["state"] == "booked"
    assert seats["A3"]["state"] == "available"

    mine = client.get("/tickets/mine", headers=user_headers)
    assert mine.status_code == 200
    assert {ticket["ticket_id"] for ticket in mine.json()} == ticket_ids
    assert {ticket["seat_number"] for ticket in mine.json()} == {"A1", "A2"}

    with SessionLocal() as db:
        rows = db.query(Ticket).filter(Ticket.event_id == event["id"]).all()
        assert len(rows) == 2
        assert len({row.seat_id for row in rows}) == 2
        assert {row.user_id for row in rows} == {user_id}
        hold_row = db.get(SeatHold, hold["hold_id"])
        assert hold_row.status == "confirmed"
        assert db.query(Seat).filter(Seat.hold_id == hold["hold_id"]).count() == 0

    refreshed = client.get(f"/holds/{hold['hold_id']}", headers=user_headers).json()
    assert refreshed["status"] == "confirmed"
    assert refreshed["seats"] == []

    again = _confirm(client, user_headers, hold["hold_id"])
    assert again.status_code == 409
    assert again.json()["detail"] == "already confirmed"
    with SessionLocal() as db:
        assert db.query(Ticket).filter(Ticket.event_id == event["id"]).count() == 2


def test_cancel_releases_seats_and_is_idempotent() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [3]}]}],
    )
    ids = _seat_ids(client, event["id"], user_headers)
    hold = _hold(client, user_headers, event["id"], ids[:2]).json()

    cancelled = client.delete(f"/holds/{hold['hold_id']}", headers=user_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert [seat["label"] for seat in cancelled.json()["seats"]] == ["A1", "A2"]

    assert all(seat["state"] == "available" for seat in _seat_map(client, event["id"], user_headers).values())

    again = client.delete(f"/holds/{hold['hold_id']}", headers=user_headers)
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"
    assert again.json()["seats"] == []

    confirmed_hold = _hold(client, user_headers, event["id"], [ids[2]]).json()
    assert _confirm(client, user_headers, confirmed_hold["hold_id"]).status_code == 201
    refused = client.delete(f"/holds/{confirmed_hold['hold_id']}", headers=user_headers)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "booking already confirmed"


def test_auto_assign_picks_the_first_seat_in_the_tier() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [
            {"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]},
            {"name": "General", "rows": [{"label": "B", "blocks": [2]}]},
        ],
    )

    first = _book(client, user_headers, event["id"], "premium", "First Buyer", "first@example.edu")
    assert first.status_code == 201, first.text
    assert first.json()["seat_number"] == "A1"
    assert first.json()["tier"] == "Premium"

    # Tier matching is case-insensitive against the admin-defined name.
    second = _book(client, user_headers, event["id"], "PREMIUM", "Second Buyer", "second@example.edu")
    assert second.status_code == 201
    assert second.json()["seat_number"] == "A2"

    general = _book(client, user_headers, event["id"], "General", "Third Buyer", "third@example.edu")
    assert general.json()["seat_number"] == "B1"

    unknown = _book(client, user_headers, event["id"], "Balcony", "Fourth Buyer", "fourth@example.edu")
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "unknown tier"

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A1"]["state"] == "booked"
    assert seats["A1"]["number"] == 1


def test_auto_assign_sold_out_tier_then_waitlist() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [
            {"name": "Premium", "rows": [{"label": "A", "blocks": [1]}]},
            {"name": "General", "rows": [{"label": "B", "blocks": [2]}]},
        ],
    )

    assert _book(client, user_headers, event["id"], "Premium", "Premium One", "p1@example.edu").status_code == 201
    sold_out = _book(client, user_headers, event["id"], "Premium", "Premium Two", "p2@example.edu")
    assert sold_out.status_code == 409
    assert sold_out.json()["detail"] == "tier sold out"

    assert _book(client, user_headers, event["id"], "General", "General One", "g1@example.edu").status_code == 201
    assert _book(client, user_headers, event["id"], "General", "General Two", "g2@example.edu").status_code == 201

    waitlisted = _book(client, user_headers, event["id"], "General", "General Three", "g3@example.edu")
    assert waitlisted.status_code == 201
    assert waitlisted.json()["outcome"] == "waitlisted"
    assert waitlisted.json()["position"] == 1

    with SessionLocal() as db:
        entry = db.get(WaitlistEntry, waitlisted.json()["waitlist_entry_id"])
        # A seat-driven queue does not pretend to be waiting for a specific tier.
        assert entry.tier == "any"


def test_event_waitlist_needs_a_full_house() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [1]}]}],
    )

    payload = {
        "attendee_name": "Queue Person",
        "attendee_contact": "queue@example.edu",
        "campus_id": "CAMP-QUEUE",
    }
    early = client.post(f"/events/{event['id']}/waitlist", json=payload, headers=user_headers)
    assert early.status_code == 409
    assert early.json()["detail"] == "seats are still available"

    assert _book(client, user_headers, event["id"], "Premium", "Seat Filler", "filler@example.edu").status_code == 201

    joined = client.post(f"/events/{event['id']}/waitlist", json=payload, headers=user_headers)
    assert joined.status_code == 201, joined.text
    assert joined.json()["outcome"] == "waitlisted"
    assert joined.json()["position"] == 1

    duplicate = client.post(f"/events/{event['id']}/waitlist", json=payload, headers=user_headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "already on the waitlist"

    legacy = client.post("/events/1/waitlist", json=payload, headers=user_headers)
    assert legacy.status_code == 409
    assert legacy.json()["detail"] == "this event has no seat map"

    # A hold makes a seat unavailable, so a fully held room accepts the queue.
    other = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "C", "blocks": [1]}]}],
        title="Held Out Event",
    )
    hold_ids = _seat_ids(client, other["id"], user_headers)
    assert _hold(client, user_headers, other["id"], hold_ids).status_code == 201
    held = client.post(f"/events/{other['id']}/waitlist", json=payload, headers=user_headers)
    assert held.status_code == 201, held.text
    assert held.json()["outcome"] == "waitlisted"


def test_revoke_frees_the_seat() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]}],
    )

    ticket = _book(client, user_headers, event["id"], "Premium", "Revoke Me", "revoke@example.edu").json()
    assert ticket["seat_number"] == "A1"

    revoked = client.post(f"/tickets/{ticket['ticket_id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["promoted_attendee"] is None

    with SessionLocal() as db:
        row = db.get(Ticket, ticket["ticket_id"])
        assert row.seat_id is None
        assert row.seat_number == "A1"

    seats = _seat_map(client, event["id"], user_headers)
    assert seats["A1"]["state"] == "available"

    mine = client.get("/tickets/mine", headers=user_headers).json()
    assert [item["ticket_id"] for item in mine] == [ticket["ticket_id"]]
    assert mine[0]["status"] == "revoked"
    assert mine[0]["seat_number"] == "A1"


def test_revoke_promotes_the_waitlist_into_the_freed_seat() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    holder_headers = auth_headers(client, "attendee", "attendee123")
    queued_headers = _register_user(client, "second")
    queued_id = client.get("/auth/me", headers=queued_headers).json()["id"]
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [1]}]}],
    )

    ticket = _book(client, holder_headers, event["id"], "Premium", "Seat Holder", "seat@example.edu").json()
    join = client.post(
        f"/events/{event['id']}/waitlist",
        json={"attendee_name": "Next In Line", "attendee_contact": "next@example.edu", "campus_id": "CAMP-NEXT"},
        headers=queued_headers,
    )
    assert join.status_code == 201, join.text
    entry_id = join.json()["waitlist_entry_id"]

    revoked = client.post(f"/tickets/{ticket['ticket_id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    promoted = revoked.json()["promoted_attendee"]
    assert promoted is not None
    assert promoted["attendee_name"] == "Next In Line"
    assert promoted["tier"] == "Premium"

    with SessionLocal() as db:
        seat = db.query(Seat).filter(Seat.event_id == event["id"]).one()
        entry = db.get(WaitlistEntry, entry_id)
        promoted_ticket = db.get(Ticket, promoted["new_ticket_id"])

        assert entry.status == "promoted"
        assert entry.promoted_ticket_id == promoted_ticket.id
        assert promoted_ticket.seat_id == seat.id
        assert promoted_ticket.seat_number == seat.label == "A1"
        assert promoted_ticket.tier == "Premium"
        assert promoted_ticket.user_id == queued_id
        # The revoked row keeps its label but no longer owns the seat.
        assert db.get(Ticket, ticket["ticket_id"]).seat_id is None
        # The queue is empty, so there is nothing left to renumber.
        assert db.query(WaitlistEntry).filter(WaitlistEntry.event_id == event["id"], WaitlistEntry.status == "waiting").count() == 0

    seats = _seat_map(client, event["id"], holder_headers)
    assert seats["A1"]["state"] == "booked"


def test_scanning_a_seat_booked_ticket() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "Premium", "rows": [{"label": "A", "blocks": [1]}]}],
    )

    ticket = _book(client, user_headers, event["id"], "Premium", "Scan Me", "scan@example.edu").json()

    gate = client.post(
        "/gates",
        json={"event_id": event["id"], "name": "Seat Gate", "location": "Door 5", "volunteer_name": "Seat Volunteer"},
        headers=admin_headers,
    )
    assert gate.status_code == 201
    gate_id = gate.json()["id"]
    volunteers = client.get("/volunteers", headers=scanner_headers).json()
    volunteer_id = next(item["id"] for item in volunteers if item["gate_id"] == gate_id)

    accepted = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": gate_id, "volunteer_id": volunteer_id},
        headers=scanner_headers,
    )
    assert accepted.status_code == 200
    assert accepted.json()["result"] == "valid"
    assert accepted.json()["seat_number"] == "A1"

    duplicate = client.post(
        "/scans",
        json={"qr_signature": ticket["qr_signature"], "gate_id": gate_id, "volunteer_id": volunteer_id},
        headers=scanner_headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["result"] == "duplicate"


def test_stats_and_csv_cover_legacy_and_seat_map_events() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    user_headers = auth_headers(client, "attendee", "attendee123")

    legacy_ticket = issue_demo_ticket(client, user_headers)
    legacy_stats = client.get("/events/1/stats", headers=admin_headers)
    assert legacy_stats.status_code == 200
    assert [tier["tier"] for tier in legacy_stats.json()["tier_breakdown"]] == ["premium"]

    legacy_csv = client.get("/events/1/export", headers=admin_headers)
    assert legacy_csv.status_code == 200
    assert "PRE-001" in legacy_csv.text
    assert "Attendee Name" in legacy_csv.text

    event = _create_seat_map_event(
        client,
        admin_headers,
        [
            {"name": "Premium", "rows": [{"label": "A", "blocks": [2]}]},
            {"name": "General", "rows": [{"label": "B", "blocks": [2]}]},
        ],
    )
    ids = _seat_ids(client, event["id"], user_headers)
    general_block = _seat_map(client, event["id"], user_headers)["B1"]["id"]
    hold = _hold(client, user_headers, event["id"], [general_block]).json()
    assert _confirm(client, user_headers, hold["hold_id"], name="Stats Person", contact="stats@example.edu").status_code == 201

    stats = client.get(f"/events/{event['id']}/stats", headers=admin_headers)
    assert stats.status_code == 200
    body = stats.json()
    assert body["capacity"] == 4
    assert body["issued"] == 1
    assert body["remaining"] == 3
    # Both layout tiers are listed in layout order, including the unsold one.
    assert [(tier["tier"], tier["issued"]) for tier in body["tier_breakdown"]] == [
        ("Premium", 0),
        ("General", 1),
    ]
    assert body["tickets"][0]["seat_number"] == "B1"
    assert len(ids) == 4

    seat_csv = client.get(f"/events/{event['id']}/export", headers=admin_headers)
    assert seat_csv.status_code == 200
    assert "B1" in seat_csv.text
    assert "General" in seat_csv.text


def _future_datetime(days: int = 7, *, hour: int = 18) -> str:
    value = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=days)
    return value.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _event_payload(
    title: str,
    days_from_now: int = 7,
    *,
    artist: str | None = None,
    city: str | None = None,
    category: str | None = None,
    venue: str = "Open Grounds",
    capacity: int = 100,
    description: str = "",
) -> dict:
    return {
        "title": title,
        "description": description,
        "date_time": _future_datetime(days_from_now),
        "venue": venue,
        "capacity": capacity,
        "artist": artist,
        "city": city,
        "category": category,
    }


def _create_event(
    client: TestClient, headers: dict[str, str], title: str, days_from_now: int = 7, **kwargs
) -> dict:
    response = client.post(
        "/events", json=_event_payload(title, days_from_now, **kwargs), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def _search(client: TestClient, **params) -> dict:
    query = {key: value for key, value in params.items() if value is not None}
    response = client.get("/events/search", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_search_normalises_metadata_and_matches_case_insensitively() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    event = _create_event(
        client,
        admin_headers,
        "  Midnight  Sessions  ",
        days_from_now=4,
        artist="  Coldplay  ",
        city="  New   Delhi  ",
        category="  Live   Music  ",
        venue="  Marine   Drive  ",
        capacity=120,
    )
    assert event["title"] == "Midnight  Sessions"
    assert event["artist"] == "Coldplay"
    assert event["city"] == "New Delhi"
    assert event["category"] == "Live Music"
    assert event["venue"] == "Marine   Drive"

    for query in ("coldplay", "  coldplay  ", "COLDPLAY", "cold"):
        body = _search(client, q=query)
        assert [item["id"] for item in body["items"]] == [event["id"]]


def test_search_ranks_exact_prefix_and_typo_matches() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    partial = _create_event(
        client, admin_headers, "Coldplay Tribute", days_from_now=2, artist="The Pretenders"
    )
    exact = _create_event(client, admin_headers, "Coldplay", days_from_now=9, artist="Coldplay")
    typo = _create_event(
        client, admin_headers, "Coldplai Revival", days_from_now=5, artist="Coldplai"
    )

    body = _search(client, q="coldplay")
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [exact["id"], partial["id"], typo["id"]]


def test_search_covers_artist_city_category_and_venue_fields() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    concert = _create_event(
        client,
        admin_headers,
        "Stadium Night",
        days_from_now=6,
        artist="The Weeknd",
        city="Bengaluru",
        category="Music",
        venue="Marine Drive",
        capacity=300,
    )
    _create_event(
        client,
        admin_headers,
        "Hands On Lab",
        days_from_now=10,
        artist="Circuitry Club",
        city="Kolkata",
        category="Workshop",
        venue="Innovation Hub",
        capacity=40,
    )

    for query in ("weeknd", "marine drive", "bengaluru", "music"):
        body = _search(client, q=query)
        assert [item["id"] for item in body["items"]] == [concert["id"]]

    assert [item["id"] for item in _search(client, city="bengaluru")["items"]] == [concert["id"]]

    filtered = _search(client, q="  night  ", city="BENGALURU", category="MUSIC")
    assert [item["id"] for item in filtered["items"]] == [concert["id"]]

    assert _search(client, city="bengaluru", category="workshop")["total"] == 0


def test_search_paginates_without_duplicates() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    created = [
        _create_event(client, admin_headers, f"Paged Event {index}", days_from_now=index)["id"]
        for index in range(1, 6)
    ]

    first = _search(client, q="paged", page=1, page_size=3)
    assert first["total"] == 5
    assert [item["id"] for item in first["items"]] == created[:3]

    second = _search(client, q="paged", page=2, page_size=3)
    assert second["total"] == 5
    assert [item["id"] for item in second["items"]] == created[3:]
    assert {item["id"] for item in first["items"]}.isdisjoint({item["id"] for item in second["items"]})

    catalogue = client.get("/events")
    assert catalogue.status_code == 200
    assert {item["id"] for item in catalogue.json()} == {1, *created}

    assert client.get("/events/search", params={"page_size": 51}).status_code == 422
    assert client.get("/events/search", params={"page": 0}).status_code == 422


def test_search_includes_past_and_cancelled_events() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    past = _create_event(
        client, admin_headers, "Archived Gathering", days_from_now=-2, artist="Yesterday Band"
    )
    future = _create_event(
        client, admin_headers, "Upcoming Gathering", days_from_now=3, artist="Tomorrow Band"
    )

    body = _search(client, q="gathering")
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {past["id"], future["id"]}
    catalogue = client.get("/events")
    assert catalogue.status_code == 200
    assert past["id"] in {item["id"] for item in catalogue.json()}

    cancelled = client.patch(
        f"/events/{future['id']}", json={"is_cancelled": True}, headers=admin_headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["is_cancelled"] is True
    assert _search(client, q="gathering")["total"] == 2

    restored = client.patch(
        f"/events/{future['id']}", json={"is_cancelled": False}, headers=admin_headers
    )
    assert restored.status_code == 200, restored.text
    assert {item["id"] for item in _search(client, q="gathering")["items"]} == {past["id"], future["id"]}


def test_search_ranks_token_prefix_above_substring() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    robotics = _create_event(
        client, admin_headers, "Robotics Workshop", days_from_now=-18
    )
    astronomy = _create_event(
        client, admin_headers, "Astronomy Night", days_from_now=10
    )

    body = _search(client, q="ro")
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [robotics["id"], astronomy["id"]]


def test_search_handles_odd_query_inputs_and_filters() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    first = _create_event(client, admin_headers, "Filter One", days_from_now=2, artist="Alpha")
    second = _create_event(client, admin_headers, "Filter Two", days_from_now=4, artist="Beta")

    assert _search(client, q="")["total"] == 3

    for query in ("'; DROP TABLE events; --", "<script>alert(1)</script>", "coldplay*"):
        assert _search(client, q=query)["total"] == 0

    assert [item["id"] for item in _search(client, date=first["date_time"][:10])["items"]] == [
        first["id"]
    ]
    assert [item["id"] for item in _search(client, date=second["date_time"][:10])["items"]] == [
        second["id"]
    ]

    last_page = _search(client, page=99)
    assert last_page["items"] == []
    assert last_page["total"] == 3


def test_search_category_and_city_match_aliases() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    bengaluru = _create_event(
        client,
        admin_headers,
        "Stadium Night",
        days_from_now=6,
        artist="The Weeknd",
        city="Bengaluru",
        category="Concert",
        capacity=300,
    )
    _create_event(
        client,
        admin_headers,
        "Robotics Lab",
        days_from_now=7,
        artist="Circuitry Club",
        city="Kolkata",
        category="Technology",
        capacity=40,
    )

    # Normalised aliases: "music" also matches a stored "Concert".
    assert [item["id"] for item in _search(client, category="music")["items"]] == [
        bengaluru["id"]
    ]
    # "bangalore" matches the stored "Bengaluru" (and vice versa).
    assert [item["id"] for item in _search(client, city="bangalore")["items"]] == [
        bengaluru["id"]
    ]
    assert [item["id"] for item in _search(client, city="Bengaluru")["items"]] == [
        bengaluru["id"]
    ]
    # Exact still governs outside the alias map: "technology" is not "music".
    assert _search(client, city="bangalore", category="technology")["total"] == 0


def test_facets_exposes_distinct_categories_and_cities() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    _create_event(client, admin_headers, "Music Night", days_from_now=2, city="Bengaluru", category="Concert")
    _create_event(client, admin_headers, "Lab", days_from_now=3, city="Kolkata", category="Workshop")
    _create_event(client, admin_headers, "No Metadata", days_from_now=4)

    response = client.get("/events/facets")
    assert response.status_code == 200, response.text
    data = response.json()
    # Plus the seeded "Techno Cultural Fest 2026" row.
    assert set(data["categories"]) == {"Concert", "Workshop", "festival"}
    assert set(data["cities"]) == {"Bengaluru", "Kolkata"}


def test_backfill_fills_only_missing_metadata() -> None:
    reset_database()
    db = SessionLocal()
    try:
        db.add(
            Event(
                title="Robotics Workshop",
                date_time=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=3),
                venue="Hall",
                capacity=50,
            )
        )
        db.flush()
        backfill_demo_metadata(db)

        filled = db.query(Event).filter(Event.title == "Robotics Workshop").one()
        assert filled.city == "Kolkata"
        assert filled.category == "workshop"

        # Already-filled values are never overwritten.
        kept = db.query(Event).filter(Event.title == "Techno Cultural Fest 2026").one()
        kept.city = "Pune"
        kept.category = None
        backfill_demo_metadata(db)
        assert kept.city == "Pune"
        assert kept.category == "festival"
        db.rollback()
    finally:
        db.close()


def test_admin_can_update_event_metadata_and_clear_optional_fields() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")
    attendee_headers = auth_headers(client, "attendee", "attendee123")

    event = _create_event(
        client,
        admin_headers,
        "Original Title",
        days_from_now=5,
        artist="Original Artist",
        city="Pune",
        category="Theatre",
    )

    updated = client.patch(
        f"/events/{event['id']}",
        json={"title": "  Revised Title  ", "artist": "  New Artist  "},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "Revised Title"
    assert body["artist"] == "New Artist"
    assert body["city"] == "Pune"
    assert [item["id"] for item in _search(client, q="revised")["items"]] == [event["id"]]
    assert [item["id"] for item in _search(client, q="new artist")["items"]] == [event["id"]]

    cleared = client.patch(
        f"/events/{event['id']}",
        json={"artist": None, "city": "   ", "category": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["artist"] is None
    assert cleared.json()["city"] is None
    assert cleared.json()["category"] is None

    assert (
        client.patch("/events/999999", json={"title": "Ghost"}, headers=admin_headers).status_code
        == 404
    )
    assert (
        client.patch(f"/events/{event['id']}", json={"title": None}, headers=admin_headers).status_code
        == 422
    )
    assert (
        client.patch(
            f"/events/{event['id']}", json={"title": "Nope"}, headers=attendee_headers
        ).status_code
        == 403
    )

    unchanged = client.patch(f"/events/{event['id']}", json={}, headers=admin_headers)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["title"] == "Revised Title"


def test_seat_map_events_reject_capacity_updates() -> None:
    reset_database()
    client = TestClient(app)
    admin_headers = auth_headers(client, "admin", "admin123")

    event = _create_seat_map_event(
        client,
        admin_headers,
        [{"name": "General", "rows": [{"label": "A", "blocks": [2]}]}],
        title="Layout Capacity",
    )
    assert event["capacity"] == 2

    rejected = client.patch(
        f"/events/{event['id']}", json={"capacity": 10}, headers=admin_headers
    )
    assert rejected.status_code == 409, rejected.text
    assert (
        rejected.json()["detail"] == "capacity is derived from the seat layout for this event"
    )

    allowed = client.patch(f"/events/{event['id']}", json={"capacity": 2}, headers=admin_headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["capacity"] == 2


def test_migrate_existing_database_adds_search_columns_idempotently(tmp_path) -> None:
    legacy_path = tmp_path / "legacy-search.db"
    legacy_engine = create_engine(f"sqlite:///{legacy_path.as_posix()}")
    with legacy_engine.begin() as setup:
        setup.execute(
            text(
                "CREATE TABLE events ("
                "event_id INTEGER PRIMARY KEY, "
                "title VARCHAR(160) NOT NULL, "
                "description TEXT NOT NULL, "
                "date_time DATETIME NOT NULL, "
                "venue VARCHAR(160) NOT NULL, "
                "capacity INTEGER NOT NULL)"
            )
        )
        setup.execute(
            text(
                "INSERT INTO events (event_id, title, description, date_time, venue, capacity) "
                "VALUES (1, 'Legacy Search Fest', '', '2026-10-01T10:00:00', 'Hall A', 500)"
            )
        )

    original_database_url = database_module.DATABASE_URL
    original_engine = database_module.engine
    database_module.DATABASE_URL = f"sqlite:///{legacy_path.as_posix()}"
    database_module.engine = legacy_engine
    try:
        database_module.migrate_existing_database()
        database_module.migrate_existing_database()
        columns = {column["name"] for column in inspect(legacy_engine).get_columns("events")}
        with legacy_engine.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT title, artist, city, category, is_cancelled "
                    "FROM events WHERE event_id = 1"
                )
            ).mappings().one()
    finally:
        database_module.DATABASE_URL = original_database_url
        database_module.engine = original_engine
        legacy_engine.dispose()
        legacy_path.unlink(missing_ok=True)

    assert {"artist", "city", "category", "is_cancelled"} <= columns
    assert row["title"] == "Legacy Search Fest"
    assert row["artist"] is None
    assert row["city"] is None
    assert row["category"] is None
    assert row["is_cancelled"] == 0

