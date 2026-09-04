from fastapi.testclient import TestClient

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.seed import seed_reference_data


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


def test_protected_routes_require_login() -> None:
    reset_database()
    client = TestClient(app)

    response = client.get("/events")

    assert response.status_code == 401


def test_admin_can_create_event() -> None:
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "admin", "admin123")

    response = client.post(
        "/events",
        json={
            "title": "Freshers Night 2026",
            "date_time": "2026-09-18T18:30:00",
            "venue": "Seminar Hall",
            "capacity": 250,
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Freshers Night 2026"
    assert body["capacity"] == 250


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
