import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.database import normalize_database_url
from backend.app.models import Event, Seat


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
        ("postgresql://user:pass@host/db", "postgresql+psycopg://user:pass@host/db"),
        (
            "postgresql://user:pass@host/db?sslmode=require",
            "postgresql+psycopg://user:pass@host/db?sslmode=require",
        ),
        (
            "postgresql+psycopg://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        ("sqlite:///./ticketx.db", "sqlite:///./ticketx.db"),
    ],
)
def test_normalize_database_url_selects_psycopg(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_boolean_defaults_compile_for_postgres() -> None:
    dialect = postgresql.dialect()

    event_sql = str(CreateTable(Event.__table__).compile(dialect=dialect)).lower()
    seat_sql = str(CreateTable(Seat.__table__).compile(dialect=dialect)).lower()

    assert "has_seat_map boolean default false not null" in event_sql
    assert "is_blocked boolean default false not null" in seat_sql
