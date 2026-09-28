import logging
import os
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


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
        # TicketX deploys on SQLite. If DATABASE_URL is ever pointed at another
        # engine the ALTERs below silently do not run, so say so loudly rather
        # than leaving a half-migrated schema to fail at request time.
        logger.warning(
            "Skipping schema migration: migrate_existing_database() only supports SQLite, "
            "but DATABASE_URL is %r. New columns will NOT be added to existing tables.",
            DATABASE_URL.split("://", 1)[0],
        )
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        if "users" in tables:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "google_sub" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(64)"))
            if "email" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(160)"))

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
            if "revoked_at" not in ticket_columns:
                connection.execute(text("ALTER TABLE tickets ADD COLUMN revoked_at DATETIME"))
            if "user_id" not in ticket_columns:
                connection.execute(text("ALTER TABLE tickets ADD COLUMN user_id INTEGER"))

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

            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_tickets_user_id ON tickets (user_id)")
            )

        if "waitlist_entries" in tables:
            waitlist_columns = {column["name"] for column in inspector.get_columns("waitlist_entries")}
            if "attendee_id" not in waitlist_columns:
                connection.execute(text("ALTER TABLE waitlist_entries ADD COLUMN attendee_id INTEGER"))
            if "user_id" not in waitlist_columns:
                connection.execute(text("ALTER TABLE waitlist_entries ADD COLUMN user_id INTEGER"))
            if "promoted_ticket_id" not in waitlist_columns:
                connection.execute(text("ALTER TABLE waitlist_entries ADD COLUMN promoted_ticket_id INTEGER"))
            if "resolved_at" not in waitlist_columns:
                connection.execute(text("ALTER TABLE waitlist_entries ADD COLUMN resolved_at DATETIME"))

            # Backfill ET-07 links for entries created before this story. An
            # Attendee row is matched on campus_id, falling back to the lowercased
            # contact, which is the same identity key issuance uses. Entries that
            # match nothing keep attendee_id NULL and still work positionally.
            linkable = connection.execute(
                text(
                    "SELECT id, attendee_contact, campus_id FROM waitlist_entries "
                    "WHERE attendee_id IS NULL"
                )
            ).mappings()
            for row in linkable:
                identity = (row["campus_id"] or "").strip() or (row["attendee_contact"] or "").strip().lower()
                if not identity:
                    continue
                match = connection.execute(
                    text("SELECT attendee_id FROM attendees WHERE campus_id = :identity"),
                    {"identity": identity},
                ).mappings().first()
                if match is None:
                    connection.execute(
                        text(
                            "INSERT INTO attendees (name, campus_id, contact_email) "
                            "VALUES (:name, :campus_id, :contact_email)"
                        ),
                        {
                            "name": f"Waitlist {row['id']}",
                            "campus_id": identity,
                            "contact_email": (row["attendee_contact"] or "").strip().lower(),
                        },
                    )
                    match = connection.execute(
                        text("SELECT attendee_id FROM attendees WHERE campus_id = :identity"),
                        {"identity": identity},
                    ).mappings().first()
                connection.execute(
                    text("UPDATE waitlist_entries SET attendee_id = :attendee_id WHERE id = :entry_id"),
                    {"attendee_id": match["attendee_id"], "entry_id": row["id"]},
                )

            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_waitlist_event_status_position ON waitlist_entries (event_id, status, position)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_waitlist_entries_attendee_id ON waitlist_entries (attendee_id)")
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_waitlist_entries_user_id ON waitlist_entries (user_id)")
            )
