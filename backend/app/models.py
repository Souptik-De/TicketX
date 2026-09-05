from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column("user_id", Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)


class Attendee(Base):
    __tablename__ = "attendees"

    id: Mapped[int] = mapped_column("attendee_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    campus_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    contact_email: Mapped[str] = mapped_column(String(160), nullable=False)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="attendee")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column("event_id", Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(600), default="", nullable=False)
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    venue: Mapped[str] = mapped_column(String(160), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="event")
    gates: Mapped[list["Gate"]] = relationship(back_populates="event")

    @property
    def issued_count(self) -> int:
        return len(self.tickets)

    @property
    def tier_counts(self) -> list[dict[str, str | int]]:
        counts: dict[str, int] = {}
        for ticket in self.tickets:
            counts[ticket.tier] = counts.get(ticket.tier, 0) + 1
        return [{"tier": tier, "issued_count": count} for tier, count in sorted(counts.items())]


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column("ticket_id", Integer, primary_key=True, index=True)
    qr_signature: Mapped[str | None] = mapped_column(String(180), unique=True, nullable=True)
    tier: Mapped[str] = mapped_column(String(40), default="general", nullable=False)
    seat_number: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="issued", nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.event_id"), nullable=False)
    attendee_id: Mapped[int] = mapped_column(ForeignKey("attendees.attendee_id"), nullable=False)

    event: Mapped[Event] = relationship(back_populates="tickets")
    attendee: Mapped[Attendee] = relationship(back_populates="tickets")


class Gate(Base):
    __tablename__ = "gates"

    id: Mapped[int] = mapped_column("gate_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.event_id"), nullable=True)

    event: Mapped[Event | None] = relationship(back_populates="gates")
    volunteers: Mapped[list["Volunteer"]] = relationship(back_populates="gate")


class Volunteer(Base):
    __tablename__ = "volunteers"

    id: Mapped[int] = mapped_column("volunteer_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    gate_id: Mapped[int | None] = mapped_column(ForeignKey("gates.gate_id"), nullable=True)

    gate: Mapped[Gate | None] = relationship(back_populates="volunteers")
