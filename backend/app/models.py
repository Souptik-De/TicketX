from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column("user_id", Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(160), nullable=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)

    waitlist_entries: Mapped[list["WaitlistEntry"]] = relationship(back_populates="user")
    scans: Mapped[list["Scan"]] = relationship(back_populates="user")


class Attendee(Base):
    __tablename__ = "attendees"

    id: Mapped[int] = mapped_column("attendee_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    campus_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    contact_email: Mapped[str] = mapped_column(String(160), nullable=False)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="attendee")
    waitlist_entries: Mapped[list["WaitlistEntry"]] = relationship(back_populates="attendee")


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
    waitlist_entries: Mapped[list["WaitlistEntry"]] = relationship(back_populates="event")

    @property
    def active_tickets(self) -> list["Ticket"]:
        """Tickets holding a seat. Revoked tickets have released theirs."""
        return [ticket for ticket in self.tickets if ticket.status != "revoked"]

    @property
    def issued_count(self) -> int:
        return len(self.active_tickets)

    @property
    def revoked_count(self) -> int:
        return len(self.tickets) - len(self.active_tickets)

    @property
    def tier_counts(self) -> list[dict[str, str | int]]:
        counts: dict[str, int] = {}
        for ticket in self.active_tickets:
            counts[ticket.tier] = counts.get(ticket.tier, 0) + 1
        return [{"tier": tier, "issued_count": count} for tier, count in sorted(counts.items())]


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column("ticket_id", Integer, primary_key=True, index=True)
    qr_signature: Mapped[str | None] = mapped_column(String(180), unique=True, nullable=True)
    tier: Mapped[str] = mapped_column(String(40), default="general", nullable=False)
    seat_number: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # status allowed values: "issued", "used", "revoked"
    status: Mapped[str] = mapped_column(String(20), default="issued", nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.event_id"), nullable=False)
    attendee_id: Mapped[int] = mapped_column(ForeignKey("attendees.attendee_id"), nullable=False)
    # The signed-in account that requested this ticket, so "my tickets" can find
    # it again after a reload. Nullable only so pre-existing rows survive the
    # migration; issuance records no owner for tickets created before this.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True, index=True)

    event: Mapped[Event] = relationship(back_populates="tickets")
    attendee: Mapped[Attendee] = relationship(back_populates="tickets")
    scans: Mapped[list["Scan"]] = relationship(back_populates="ticket")

    @property
    def outcome(self) -> str:
        return "ticketed"


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        # Every read path filters on the event, then orders by position among
        # the still-waiting rows.
        Index("ix_waitlist_event_status_position", "event_id", "status", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.event_id"), nullable=False)
    # Links the queue to the real Attendee row, created on join rather than on
    # issuance. Nullable only so pre-ET-07 rows survive the migration backfill.
    attendee_id: Mapped[int | None] = mapped_column(ForeignKey("attendees.attendee_id"), nullable=True, index=True)
    # Who submitted the request. Drives the "my registrations" lookup that
    # powers the in-app promotion notice; it is not a dedupe key, because one
    # user submits on behalf of many attendees.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True, index=True)
    # Set when ET-11 revocation promotes this entry, so the promoted attendee
    # can reach their ticket without a name-based lookup.
    promoted_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.ticket_id"), nullable=True)
    attendee_name: Mapped[str] = mapped_column(String(120), nullable=False)
    attendee_contact: Mapped[str] = mapped_column(String(160), nullable=False)
    campus_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tier: Mapped[str] = mapped_column(String(40), default="general", nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # status allowed values: "waiting", "promoted", "cancelled"
    status: Mapped[str] = mapped_column(String(20), default="waiting", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    event: Mapped[Event] = relationship(back_populates="waitlist_entries")
    attendee: Mapped["Attendee"] = relationship(back_populates="waitlist_entries")
    user: Mapped["User"] = relationship(back_populates="waitlist_entries")
    promoted_ticket: Mapped["Ticket"] = relationship()

    @property
    def outcome(self) -> str:
        return "waitlisted"

    @property
    def waitlisted(self) -> bool:
        return True


class Gate(Base):
    __tablename__ = "gates"

    id: Mapped[int] = mapped_column("gate_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.event_id"), nullable=True)

    event: Mapped[Event | None] = relationship(back_populates="gates")
    volunteers: Mapped[list["Volunteer"]] = relationship(back_populates="gate")
    scans: Mapped[list["Scan"]] = relationship(back_populates="gate")


class Volunteer(Base):
    __tablename__ = "volunteers"

    id: Mapped[int] = mapped_column("volunteer_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    gate_id: Mapped[int | None] = mapped_column(ForeignKey("gates.gate_id"), nullable=True)

    gate: Mapped[Gate | None] = relationship(back_populates="volunteers")
    scans: Mapped[list["Scan"]] = relationship(back_populates="volunteer")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column("scan_id", Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.ticket_id"), nullable=False)
    gate_id: Mapped[int] = mapped_column(ForeignKey("gates.gate_id"), nullable=False)
    volunteer_id: Mapped[int] = mapped_column(ForeignKey("volunteers.volunteer_id"), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True, index=True)

    ticket: Mapped[Ticket] = relationship(back_populates="scans")
    gate: Mapped[Gate] = relationship(back_populates="scans")
    volunteer: Mapped[Volunteer] = relationship(back_populates="scans")
    user: Mapped[User | None] = relationship(back_populates="scans")
