import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ticketx.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_database() -> None:
    if DATABASE_URL.startswith("sqlite"):
        db_path = DATABASE_URL.removeprefix("sqlite:///")
        if db_path and not db_path.startswith(":memory:"):
            folder = os.path.dirname(os.path.abspath(db_path))
            if folder:
                os.makedirs(folder, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    migrate_existing_database()


def migrate_existing_database() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        if "gates" in tables:
            gate_columns = {column["name"] for column in inspector.get_columns("gates")}
            if "event_id" not in gate_columns:
                connection.execute(text("ALTER TABLE gates ADD COLUMN event_id INTEGER"))

        if "events" in tables:
            event_columns = {column["name"] for column in inspector.get_columns("events")}
            if "description" not in event_columns:
                connection.execute(text("ALTER TABLE events ADD COLUMN description VARCHAR(600) NOT NULL DEFAULT ''"))

        if "tickets" in tables:
            ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
            if "seat_number" not in ticket_columns:
                connection.execute(text("ALTER TABLE tickets ADD COLUMN seat_number VARCHAR(24)"))

            rows = connection.execute(
                text(
                    "SELECT ticket_id, event_id, tier FROM tickets "
                    "WHERE seat_number IS NULL OR seat_number = '' ORDER BY event_id, tier, ticket_id"
                )
            ).mappings()
            counters: dict[tuple[int, str], int] = {}
            prefixes = {"general": "GEN", "premium": "PRE", "vip": "VIP", "staff": "STF"}
            for row in rows:
                tier = (row["tier"] or "general").lower()
                key = (row["event_id"], tier)
                counters[key] = counters.get(key, 0) + 1
                seat_number = f"{prefixes.get(tier, tier[:3].upper())}-{counters[key]:03d}"
                connection.execute(
                    text("UPDATE tickets SET seat_number = :seat_number WHERE ticket_id = :ticket_id"),
                    {"seat_number": seat_number, "ticket_id": row["ticket_id"]},
                )
