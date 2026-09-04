from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Event, Gate, User, Volunteer


def seed_reference_data(db: Session) -> None:
    if db.query(User).count() == 0:
        db.add_all(
            [
                User(username="attendee", password_hash=hash_password("attendee123"), role="user", display_name="Student User"),
                User(username="admin", password_hash=hash_password("admin123"), role="admin", display_name="Event Admin"),
                User(username="scanner", password_hash=hash_password("scanner123"), role="scanner", display_name="Gate Scanner"),
            ]
        )

    if db.query(Event).count() == 0:
        db.add(
            Event(
                title="Techno Cultural Fest 2026",
                date_time=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=14),
                venue="Main Auditorium",
                capacity=500,
            )
        )
        db.flush()

    event = db.query(Event).order_by(Event.id.asc()).first()

    if db.query(Gate).count() == 0:
        main_gate = Gate(name="Main Gate", location="Auditorium front entrance", event_id=event.id if event else None)
        db.add(main_gate)
        db.flush()
        db.add(Volunteer(name="Souptik De", gate_id=main_gate.id))
    else:
        db.query(Gate).filter(Gate.event_id.is_(None)).update({"event_id": event.id if event else None})

    db.commit()
