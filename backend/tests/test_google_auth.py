import os
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["GOOGLE_CLIENT_ID"] = "test-google-client-id"

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


def fake_google_info(sub="google-123", email="riya@example.edu", name="Riya Sen"):
    return {"sub": sub, "email": email, "name": name}


def test_google_login_creates_user(monkeypatch) -> None:
    reset_database()
    import backend.app.main as main_module

    monkeypatch.setattr(main_module, "verify_google_id_token", lambda token: fake_google_info())
    client = TestClient(app)

    first = client.post("/auth/google", json={"id_token": "fake-token", "role": "user"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["token"]
    assert body["user"]["role"] == "user"
    first_id = body["user"]["id"]

    second = client.post("/auth/google", json={"id_token": "fake-token-again", "role": "user"})
    assert second.status_code == 200
    assert second.json()["user"]["id"] == first_id


def test_google_login_blocks_admin_role() -> None:
    reset_database()
    client = TestClient(app)
    response = client.post("/auth/google", json={"id_token": "fake-token", "role": "admin"})
    assert response.status_code in (422, 403)


def test_google_login_rejects_invalid_token(monkeypatch) -> None:
    reset_database()
    import backend.app.main as main_module
    from fastapi import HTTPException, status

    def _bad(_token: str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google login")

    monkeypatch.setattr(main_module, "verify_google_id_token", _bad)
    client = TestClient(app)
    response = client.post("/auth/google", json={"id_token": "bad-token-12345"})
    assert response.status_code == 401


def test_google_login_links_existing_email_user(monkeypatch) -> None:
    reset_database()
    import backend.app.main as main_module

    client = TestClient(app)
    created = client.post(
        "/auth/register",
        json={
            "username": "riya@example.edu",
            "password": "password123",
            "display_name": "Riya Sen",
            "role": "user",
        },
    )
    assert created.status_code == 201
    created_id = created.json()["user"]["id"]

    monkeypatch.setattr(main_module, "verify_google_id_token", lambda token: fake_google_info())
    linked = client.post("/auth/google", json={"id_token": "fake-token"})
    assert linked.status_code == 200
    assert linked.json()["user"]["id"] == created_id

    with SessionLocal() as db:
        from backend.app.models import User

        user = db.get(User, created_id)
        assert user.google_sub == "google-123"
