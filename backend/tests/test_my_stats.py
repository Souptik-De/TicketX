import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app.database import Base, SessionLocal, engine  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.seed import seed_reference_data  # noqa: E402


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


def issue_ticket(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "event_id": 1,
        "attendee_name": "Riya Sen",
        "attendee_contact": "riya@example.edu",
        "tier": "premium",
    }
    payload.update(overrides)
    response = client.post("/tickets", json=payload, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_scanner_sees_own_scan_history() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    empty = client.get("/me/scans", headers=scanner_headers)
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    first = issue_ticket(client, user_headers)
    second = issue_ticket(client, user_headers, attendee_name="Amit", attendee_contact="amit@example.edu", tier="general")

    ok_scan = client.post(
        "/scans",
        json={"qr_signature": first["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert ok_scan.status_code == 200
    assert ok_scan.json()["result"] == "valid"

    dup_scan = client.post(
        "/scans",
        json={"qr_signature": first["qr_signature"], "gate_id": 2, "volunteer_id": 3},
        headers=scanner_headers,
    )
    assert dup_scan.json()["result"] == "duplicate"

    stats = client.get("/me/scans", headers=scanner_headers)
    assert stats.status_code == 200
    body = stats.json()
    assert body["total"] == 2
    assert body["valid"] == 1
    assert body["duplicate"] == 1
    assert body["invalid"] == 0
    assert body["gates"] == [
        {"gate_id": 1, "name": "Main Gate", "location": "Auditorium front entrance", "scanned_count": 1}
    ]
    assert [item["ticket_id"] for item in body["recent"]] == [first["ticket_id"], first["ticket_id"]]
    assert body["recent"][0]["attendee_name"] == "Riya Sen"
    assert second["ticket_id"] not in [item["ticket_id"] for item in body["recent"]]


def test_scan_history_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    admin_headers = auth_headers(client, "admin", "admin123")

    assert client.get("/me/scans").status_code == 401
    assert client.get("/me/scans", headers=user_headers).status_code == 403
    assert client.get("/me/scans", headers=admin_headers).status_code == 403


def test_user_sees_ticket_stats_with_checkin_gate() -> None:
    reset_database()
    client = TestClient(app)
    user_headers = auth_headers(client, "attendee", "attendee123")
    scanner_headers = auth_headers(client, "scanner", "scanner123")

    empty = client.get("/me/ticket-stats", headers=user_headers)
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    first = issue_ticket(client, user_headers)
    issue_ticket(client, user_headers, attendee_name="Amit", attendee_contact="amit@example.edu", tier="general")

    scan = client.post(
        "/scans",
        json={"qr_signature": first["qr_signature"], "gate_id": 1, "volunteer_id": 1},
        headers=scanner_headers,
    )
    assert scan.json()["result"] == "valid"

    stats = client.get("/me/ticket-stats", headers=user_headers)
    assert stats.status_code == 200
    body = stats.json()
    assert body["total"] == 2
    assert body["used"] == 1
    assert body["valid"] == 1
    assert body["tiers"] == [
        {"tier": "general", "count": 1},
        {"tier": "premium", "count": 1},
    ]
    checked = next(item for item in body["tickets"] if item["ticket_id"] == first["ticket_id"])
    assert checked["checked_in"] is True
    assert checked["check_gate_name"] == "Main Gate"
    assert checked["checked_at"] is not None
    pending = next(item for item in body["tickets"] if item["checked_in"] is False)
    assert pending["check_gate_name"] is None


def test_ticket_stats_role_protection() -> None:
    reset_database()
    client = TestClient(app)
    scanner_headers = auth_headers(client, "scanner", "scanner123")
    admin_headers = auth_headers(client, "admin", "admin123")

    assert client.get("/me/ticket-stats").status_code == 401
    assert client.get("/me/ticket-stats", headers=scanner_headers).status_code == 403
    assert client.get("/me/ticket-stats", headers=admin_headers).status_code == 403
