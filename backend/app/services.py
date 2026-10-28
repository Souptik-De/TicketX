import csv
from datetime import UTC, date, datetime, timedelta
import difflib
import io

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, subqueryload

from .config import MAX_SEATS_PER_BOOKING, get_seat_hold_seconds
from .models import Attendee, Event, Gate, Scan, Seat, SeatHold, Ticket, User, Volunteer, WaitlistEntry
from .schemas import (
    AttendeeOut,
    ConfirmHoldOut,
    EventCreate,
    EventOut,
    EventUpdate,
    EventStatsGate,
    EventStatsOut,
    EventStatsTicket,
    EventStatsTier,
    EventTierOut,
    GateCreate,
    GateStatus,
    HeldSeatOut,
    HoldOut,
    MyRegistrations,
    MyScanGate,
    MyScanItem,
    MyScanStats,
    MyTicketStatItem,
    MyTicketStats,
    MyTicketTier,
    MyWaitlistEntry,
    PriorScan,
    PromotedAttendeeInfo,
    RowOut,
    ScanCreate,
    ScanResult,
    SeatLayoutIn,
    SeatMapOut,
    SeatOut,
    TierMapOut,
    TicketCreate,
    TicketDetail,
    WaitlistEntryOut,
)
from .security import sign_ticket, ticket_id_from_signature


TIER_PREFIXES = {"general": "GEN", "premium": "PRE", "vip": "VIP"}


class _HoldNotClaimed(Exception):
    """Internal: the conditional UPDATE lost a race with confirm/expire/cancel."""


def _utcnow() -> datetime:
    """Naive UTC, the storage convention used everywhere else in this module."""
    return datetime.now(UTC).replace(tzinfo=None)



def _get_or_create_attendee(
    db: Session,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
) -> Attendee:
    """Resolve the Attendee identity for a person.

    ``campus_id`` is the natural key and is globally unique, so it is reused
    across events. When it is missing the contact email stands in, which is how
    an anonymous registration stays distinguishable from someone else.
    """
    contact = str(attendee_contact).strip().lower()
    identity = campus_id.strip() if campus_id else contact
    attendee = db.query(Attendee).filter(Attendee.campus_id == identity).one_or_none()
    if attendee is None:
        attendee = Attendee(
            name=attendee_name.strip(),
            campus_id=identity,
            contact_email=contact,
        )
        db.add(attendee)
        db.flush()
    return attendee


def _next_seat_number(db: Session, event_id: int, tier: str) -> str:
    """Allocate a seat code that is never reused.

    Revocation frees capacity, not seat numbers, so this walks past the highest
    code already handed out for the tier rather than counting live tickets --
    counting would collide with a code an existing ticket still holds.
    """
    highest = 0
    existing = db.query(Ticket).filter(Ticket.event_id == event_id, Ticket.tier == tier).all()
    for ticket in existing:
        if not ticket.seat_number or "-" not in ticket.seat_number:
            continue
        try:
            highest = max(highest, int(ticket.seat_number.rsplit("-", 1)[1]))
        except ValueError:
            continue
    return f"{TIER_PREFIXES[tier]}-{highest + 1:03d}"


def _seat_rows_from_layout(event_id: int, layout: SeatLayoutIn) -> tuple[list[dict], int]:
    """Materialise a layout into Seat insert mappings plus the blocked count.

    ``row_order`` is global across tiers so ordering by it preserves the tier
    order the admin listed without storing a separate tier index.
    """
    rows: list[dict] = []
    blocked_count = 0
    row_order = 0
    for tier in layout.tiers:
        for row in tier.rows:
            blocked = set(row.blocked)
            seat_number = 0
            for block_index, block_size in enumerate(row.blocks):
                for _ in range(block_size):
                    seat_number += 1
                    is_blocked = seat_number in blocked
                    if is_blocked:
                        blocked_count += 1
                    rows.append(
                        {
                            "event_id": event_id,
                            "tier_name": tier.name,
                            "row_label": row.label,
                            "row_order": row_order,
                            "block_index": block_index,
                            "seat_number": seat_number,
                            "label": f"{row.label}{seat_number}",
                            "is_blocked": is_blocked,
                            "hold_id": None,
                        }
                    )
            row_order += 1
    return rows, blocked_count


def create_event(db: Session, payload: EventCreate) -> EventOut:
    """Create a legacy or seat-map event.

    Seat rows are generated and inserted in the same transaction as the event,
    so a failure leaves neither half behind.
    """
    event = Event(
        title=payload.title.strip(),
        description=payload.description.strip(),
        date_time=payload.date_time,
        venue=payload.venue.strip(),
        artist=payload.artist,
        city=payload.city,
        category=payload.category,
        capacity=payload.capacity or 0,
        has_seat_map=payload.seat_layout is not None,
    )
    try:
        db.add(event)
        db.flush()

        if payload.seat_layout is not None:
            seat_rows, blocked_count = _seat_rows_from_layout(event.id, payload.seat_layout)
            if seat_rows:
                db.bulk_insert_mappings(Seat, seat_rows)
            event.capacity = len(seat_rows) - blocked_count

        db.commit()
        db.refresh(event)
        return _event_out(db, event)
    except Exception:
        db.rollback()
        raise


def release_expired_holds(db: Session, event_id: int) -> int:
    """Expire stale holds and free their seats. Returns the number released.

    Called before every seat read/hold/confirm/auto-assign. It intentionally
    does not promote anyone: an abandoned hold is not a freed *sold* seat.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    expired = (
        db.query(SeatHold)
        .filter(
            SeatHold.event_id == event_id,
            SeatHold.status == "active",
            SeatHold.expires_at <= now,
        )
        .all()
    )
    if not expired:
        return 0

    hold_ids = [hold.id for hold in expired]
    db.query(Seat).filter(Seat.hold_id.in_(hold_ids)).update(
        {Seat.hold_id: None},
        synchronize_session="fetch",
    )
    for hold in expired:
        hold.status = "expired"
    db.commit()
    return len(expired)


def _tier_summaries_for_events(db: Session, event_ids: list[int]) -> dict[int, list[EventTierOut]]:
    """Sellable/available counts per tier for several events in a few queries."""
    if not event_ids:
        return {}

    now = datetime.now(UTC).replace(tzinfo=None)
    seat_rows = (
        db.query(Seat.event_id, Seat.tier_name, Seat.id, Seat.is_blocked, Seat.hold_id)
        .filter(Seat.event_id.in_(event_ids))
        .order_by(
            Seat.event_id.asc(),
            Seat.row_order.asc(),
            Seat.block_index.asc(),
            Seat.seat_number.asc(),
        )
        .all()
    )

    booked_by_event: dict[int, set[int]] = {}
    for event_id, seat_id in (
        db.query(Ticket.event_id, Ticket.seat_id)
        .filter(
            Ticket.event_id.in_(event_ids),
            Ticket.seat_id.isnot(None),
            Ticket.status != "revoked",
        )
        .all()
    ):
        booked_by_event.setdefault(event_id, set()).add(seat_id)

    active_holds_by_event: dict[int, set[int]] = {}
    for hold_id, hold_event_id in (
        db.query(SeatHold.id, SeatHold.event_id)
        .filter(
            SeatHold.event_id.in_(event_ids),
            SeatHold.status == "active",
            SeatHold.expires_at > now,
        )
        .all()
    ):
        active_holds_by_event.setdefault(hold_event_id, set()).add(hold_id)

    counts: dict[int, dict[str, dict[str, int]]] = {}
    for row in seat_rows:
        tiers = counts.setdefault(row.event_id, {})
        tier = tiers.setdefault(row.tier_name, {"total": 0, "available": 0})
        if row.is_blocked:
            continue
        tier["total"] += 1
        booked = booked_by_event.get(row.event_id, set())
        held = active_holds_by_event.get(row.event_id, set())
        if row.id not in booked and row.hold_id not in held:
            tier["available"] += 1

    return {
        event_id: [
            EventTierOut(name=name, total=tier["total"], available=tier["available"])
            for name, tier in tiers.items()
        ]
        for event_id, tiers in counts.items()
    }


def _event_out(
    db: Session,
    event: Event,
    tier_summaries: list[EventTierOut] | None = None,
) -> EventOut:
    out = EventOut.model_validate(event)
    if event.has_seat_map:
        if tier_summaries is None:
            tier_summaries = _tier_summaries_for_events(db, [event.id]).get(event.id, [])
        out.tiers = tier_summaries
    return out


def list_events(db: Session) -> list[EventOut]:
    """Public catalog, with sellable/available tier counts for seat maps."""
    seat_map_ids = [
        event_id
        for (event_id,) in db.query(Event.id).filter(Event.has_seat_map.is_(True)).all()
    ]
    for event_id in seat_map_ids:
        release_expired_holds(db, event_id)

    events = (
        db.query(Event)
        .options(subqueryload(Event.tickets))
        .order_by(Event.date_time.asc())
        .all()
    )
    summaries = _tier_summaries_for_events(db, seat_map_ids)
    return [_event_out(db, event, summaries.get(event.id, [])) for event in events]


def _normalise_search_text(value: str) -> str:
    """Lowercase, trim and collapse whitespace for lenient matching."""
    return " ".join(value.casefold().split())


def _match_score(query: str, candidate: str) -> float:
    """Score one field against the query, from exact down to a typo match."""
    if not candidate:
        return 0.0
    if candidate == query:
        return 1.0
    if candidate.startswith(query):
        return 0.85
    tokens = candidate.split()
    if any(token.startswith(query) for token in tokens):
        return 0.7
    if query in candidate:
        return 0.55
    ratio = difflib.SequenceMatcher(None, query, candidate).ratio()
    token_ratio = max(
        (difflib.SequenceMatcher(None, query, token).ratio() for token in tokens),
        default=0.0,
    )
    best = max(ratio, token_ratio)
    return best * 0.5 if best >= 0.6 else 0.0


_EVENT_SEARCH_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("title", 6.0),
    ("artist", 4.0),
    ("venue", 3.0),
    ("city", 2.0),
    ("category", 2.0),
)


def _event_search_score(query: str, event: Event) -> float:
    """Weight the best field match so titles outrank secondary metadata."""
    best = 0.0
    for field_name, weight in _EVENT_SEARCH_WEIGHTS:
        value = getattr(event, field_name)
        if not value:
            continue
        best = max(best, weight * _match_score(query, _normalise_search_text(value)))
    return best


def search_events(
    db: Session,
    q: str = "",
    city: str | None = None,
    category: str | None = None,
    event_date: date | None = None,
    page: int = 1,
    page_size: int = 6,
) -> tuple[list[EventOut], int]:
    """Public search over the whole catalog, tolerant of partial words and typos."""
    query = (
        db.query(Event)
        .options(subqueryload(Event.tickets))
    )

    city_filter = _normalise_search_text(city) if city else ""
    if city_filter:
        query = query.filter(func.lower(Event.city) == city_filter)

    category_filter = _normalise_search_text(category) if category else ""
    if category_filter:
        query = query.filter(func.lower(Event.category) == category_filter)

    if event_date is not None:
        start = datetime.combine(event_date, datetime.min.time())
        query = query.filter(
            Event.date_time >= start,
            Event.date_time < start + timedelta(days=1),
        )

    events = query.all()
    query_text = _normalise_search_text(q)
    if query_text:
        scored = [
            (score, event)
            for event in events
            if (score := _event_search_score(query_text, event)) > 0
        ]
        scored.sort(key=lambda item: (-item[0], item[1].date_time, item[1].id))
        ranked = [event for _, event in scored]
    else:
        ranked = sorted(events, key=lambda event: (event.date_time, event.id))

    total = len(ranked)
    offset = (page - 1) * page_size
    page_events = ranked[offset : offset + page_size]

    seat_map_ids = [event.id for event in page_events if event.has_seat_map]
    for event_id in seat_map_ids:
        release_expired_holds(db, event_id)
    summaries = _tier_summaries_for_events(db, seat_map_ids)
    return (
        [_event_out(db, event, summaries.get(event.id, [])) for event in page_events],
        total,
    )


def update_event(db: Session, event_id: int, payload: EventUpdate) -> EventOut:
    """Apply a partial admin edit and return the refreshed event."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    changes = payload.model_dump(exclude_unset=True)
    if (
        "capacity" in changes
        and event.has_seat_map
        and changes["capacity"] != event.capacity
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="capacity is derived from the seat layout for this event",
        )

    for field_name in ("title", "description", "venue"):
        value = changes.get(field_name)
        if isinstance(value, str):
            changes[field_name] = value.strip()

    try:
        for field_name, value in changes.items():
            setattr(event, field_name, value)
        db.commit()
        db.refresh(event)
        return _event_out(db, event)
    except Exception:
        db.rollback()
        raise


def _seat_map_event(db: Session, event_id: int) -> Event:
    """Load an event and insist it has a seat map.

    Every seat-level endpoint answers the same way for a legacy event, so the
    409 message lives here rather than being repeated at each call site.
    """
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if not event.has_seat_map:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this event has no seat map",
        )
    return event


def _seat_states(
    db: Session,
    event_id: int,
) -> tuple[list[Seat], set[int], dict[int, int]]:
    """Seats plus the two things that make one unavailable.

    Returns the seats in map order, the ids of seats a live ticket points at,
    and ``{hold_id: user_id}`` for holds that have not expired. One query per
    fact, so the map, the availability checks and the auto-assign picker all
    share it instead of each inventing its own definition of "free".
    """
    seats = (
        db.query(Seat)
        .filter(Seat.event_id == event_id)
        .order_by(Seat.row_order.asc(), Seat.block_index.asc(), Seat.seat_number.asc())
        .all()
    )

    booked_ids = {
        seat_id
        for (seat_id,) in (
            db.query(Ticket.seat_id)
            .filter(
                Ticket.event_id == event_id,
                Ticket.seat_id.isnot(None),
                Ticket.status != "revoked",
            )
            .all()
        )
    }

    now = _utcnow()
    active_holds = {
        hold_id: hold_user_id
        for hold_id, hold_user_id in (
            db.query(SeatHold.id, SeatHold.user_id)
            .filter(
                SeatHold.event_id == event_id,
                SeatHold.status == "active",
                SeatHold.expires_at > now,
            )
            .all()
        )
    }
    return seats, booked_ids, active_holds


def _available_seats(db: Session, event_id: int, tier_name: str | None = None) -> list[Seat]:
    """Free, sellable seats in map order, optionally narrowed to one tier."""
    seats, booked_ids, active_holds = _seat_states(db, event_id)
    wanted = tier_name.strip().casefold() if tier_name is not None else None
    available: list[Seat] = []
    for seat in seats:
        if seat.is_blocked or seat.id in booked_ids or seat.hold_id in active_holds:
            continue
        if wanted is not None and seat.tier_name.strip().casefold() != wanted:
            continue
        available.append(seat)
    return available


def _seconds_remaining(hold: SeatHold, now: datetime | None = None) -> int:
    if hold.status != "active":
        return 0
    now = now or _utcnow()
    return max(0, int((hold.expires_at - now).total_seconds()))


def _hold_seats(db: Session, hold_id: int) -> list[Seat]:
    return (
        db.query(Seat)
        .filter(Seat.hold_id == hold_id)
        .order_by(Seat.row_order.asc(), Seat.seat_number.asc())
        .all()
    )


def _hold_out(hold: SeatHold, seats: list[Seat], now: datetime | None = None) -> HoldOut:
    return HoldOut(
        hold_id=hold.id,
        event_id=hold.event_id,
        status=hold.status,
        seconds_remaining=_seconds_remaining(hold, now),
        seats=[
            HeldSeatOut(id=seat.id, label=seat.label, tier_name=seat.tier_name)
            for seat in seats
        ],
    )


def build_seat_map(db: Session, event_id: int, user_id: int | None) -> SeatMapOut:
    """Derive every seat's state from blocked flags, tickets, and active holds."""
    _seat_map_event(db, event_id)

    release_expired_holds(db, event_id)

    seats, booked_ids, active_holds = _seat_states(db, event_id)


    tier_order: list[str] = []
    tier_rows: dict[str, list[RowOut]] = {}
    row_lookup: dict[tuple[str, str], RowOut] = {}
    total = available = held = booked = 0

    for seat in seats:
        if seat.tier_name not in tier_rows:
            tier_rows[seat.tier_name] = []
            tier_order.append(seat.tier_name)

        row_key = (seat.tier_name, seat.row_label)
        row_out = row_lookup.get(row_key)
        if row_out is None:
            row_out = RowOut(label=seat.row_label, blocks=[])
            row_lookup[row_key] = row_out
            tier_rows[seat.tier_name].append(row_out)

        hold_user_id = active_holds.get(seat.hold_id) if seat.hold_id is not None else None
        if seat.is_blocked:
            state = "blocked"
            mine = False
        elif seat.id in booked_ids:
            state = "booked"
            mine = False
        elif seat.hold_id in active_holds:
            state = "held"
            mine = user_id is not None and hold_user_id == user_id
        else:
            state = "available"
            mine = False

        if state != "blocked":
            total += 1
        if state == "available":
            available += 1
        elif state == "held":
            held += 1
        elif state == "booked":
            booked += 1

        while len(row_out.blocks) <= seat.block_index:
            row_out.blocks.append([])
        row_out.blocks[seat.block_index].append(
            SeatOut(
                id=seat.id,
                number=seat.seat_number,
                label=seat.label,
                state=state,
                mine=mine,
            )
        )

    return SeatMapOut(
        event_id=event_id,
        tiers=[TierMapOut(name=name, rows=tier_rows[name]) for name in tier_order],
        total=total,
        available=available,
        held=held,
        booked=booked,
    )


def join_waitlist(
    db: Session,
    event_id: int,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
    tier: str,
    user_id: int | None = None,
    attendee: Attendee | None = None,
) -> WaitlistEntry:
    """Place a person on the queue, or return the entry they already hold.

    Idempotent per (event, attendee): without this a retry, a double click, or
    one person submitting twice would occupy several positions and then be
    promoted several times, each time taking a seat from the person genuinely
    next in line.
    """
    if attendee is None:
        attendee = _get_or_create_attendee(db, attendee_name, attendee_contact, campus_id)

    existing = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.event_id == event_id,
            WaitlistEntry.attendee_id == attendee.id,
            WaitlistEntry.status == "waiting",
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    position = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
        .count()
    ) + 1
    entry = WaitlistEntry(
        event_id=event_id,
        attendee_id=attendee.id,
        user_id=user_id,
        attendee_name=attendee_name.strip(),
        attendee_contact=str(attendee_contact).strip().lower(),
        campus_id=campus_id.strip() if campus_id else None,
        tier=tier.strip().lower(),
        position=position,
        status="waiting",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def leave_waitlist(db: Session, entry: WaitlistEntry) -> None:
    """Withdraw a waiting entry and close the gap it leaves in the queue."""
    old_position = entry.position
    entry.status = "cancelled"
    entry.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()

    later = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.event_id == entry.event_id,
            WaitlistEntry.status == "waiting",
            WaitlistEntry.position > old_position,
        )
        .order_by(WaitlistEntry.position.asc())
        .all()
    )
    for later_entry in later:
        later_entry.position -= 1
    db.commit()


def _issue_ticket_record(
    db: Session,
    event_id: int,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
    tier: str,
    attendee: Attendee | None = None,
    user_id: int | None = None,
    seat: Seat | None = None,
) -> Ticket:
    """Write one ticket row and stamp its signature.

    With ``seat`` the ticket takes the seat's own tier and label, so an
    admin-defined tier such as "Balcony" survives instead of being normalised to
    one of the three legacy names. Without it the legacy ``GEN-001`` numbering
    applies unchanged.
    """
    if seat is not None:
        tier = seat.tier_name
        seat_number = seat.label
        seat_id = seat.id
    else:
        tier = tier.strip().lower()
        if tier not in TIER_PREFIXES:
            tier = "general"
        seat_number = _next_seat_number(db, event_id, tier)
        seat_id = None

    if attendee is None:
        attendee = _get_or_create_attendee(db, attendee_name, attendee_contact, campus_id)

    ticket = Ticket(
        event_id=event_id,
        attendee_id=attendee.id,
        tier=tier,
        seat_number=seat_number,
        seat_id=seat_id,
        status="issued",
        user_id=user_id,
    )
    db.add(ticket)
    db.flush()
    ticket.qr_signature = sign_ticket(ticket.id)
    db.flush()
    return ticket


def promote_next_waitlisted(
    db: Session,
    event_id: int,
    seat: Seat | None = None,
) -> PromotedAttendeeInfo | None:
    """Hand one freed seat to the person at the head of the queue.

    ET-07 / ET-11 contract for any path that frees a seat:

    * Call this exactly ONCE per seat freed. It does not re-check capacity,
      because the caller has already decided a seat is free.
    * Call it inside the same transaction as the change that freed the seat, and
      let the caller own the commit. On failure the caller's rollback undoes
      both the revocation and the promotion together.
    * A bulk revocation must therefore loop rather than call this once.

    ``seat`` is the physical seat that was freed. Seat-map events pass it so the
    promoted ticket lands on that exact seat and takes its tier; legacy events
    leave it out and keep the numbered ``GEN-001`` behaviour.

    Returns ``None`` when nobody is waiting.
    """
    entry = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
        .order_by(WaitlistEntry.position.asc())
        .first()
    )
    if entry is None:
        return None

    old_position = entry.position
    new_ticket = _issue_ticket_record(
        db=db,
        event_id=event_id,
        attendee_name=entry.attendee_name,
        attendee_contact=entry.attendee_contact,
        campus_id=entry.campus_id,
        # A seat-driven queue stores "any"; the seat itself decides the tier.
        tier=entry.tier,
        attendee=entry.attendee,
        # The queue row remembers who asked, so the promoted ticket lands in that
        # account's "my tickets" too rather than only being reachable through the
        # waitlist entry.
        user_id=entry.user_id,
        seat=seat,
    )

    entry.status = "promoted"
    entry.promoted_ticket_id = new_ticket.id
    entry.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    db.flush()

    # Close the gap so positions stay contiguous 1..n over the waiting rows.
    later = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.event_id == event_id,
            WaitlistEntry.status == "waiting",
            WaitlistEntry.position > old_position,
        )
        .order_by(WaitlistEntry.position.asc())
        .all()
    )
    for later_entry in later:
        later_entry.position -= 1
    db.flush()

    return PromotedAttendeeInfo(
        attendee_name=entry.attendee_name,
        new_ticket_id=new_ticket.id,
        tier=new_ticket.tier,
    )


def find_active_ticket_for_user(
    db: Session,
    event_id: int,
    user_id: int | None,
    attendee_id: int | None = None,
) -> Ticket | None:
    """A live ticket this account already holds for an event.

    Revoked tickets are excluded, so someone whose seat was freed and then handed
    to the waitlist can register again rather than being locked out forever.

    ``attendee_id`` narrows the check to one person. One account may still book
    several different people for the same event -- the form has always allowed
    that -- so the block is only on handing the same attendee a second seat.
    """
    if user_id is None:
        return None
    query = db.query(Ticket).filter(
        Ticket.event_id == event_id,
        Ticket.user_id == user_id,
        Ticket.status != "revoked",
    )
    if attendee_id is not None:
        query = query.filter(Ticket.attendee_id == attendee_id)
    return query.order_by(Ticket.id.asc()).first()


def create_hold(db: Session, event_id: int, user: User, seat_ids: list[int]) -> HoldOut:
    """Claim seats for a short window while the attendee details are filled in.

    All-or-nothing: the claim is one conditional UPDATE, and a short rowcount
    rolls the whole transaction back -- including the cancellation of this
    user's previous hold, which is restored by the rollback.
    """
    _seat_map_event(db, event_id)

    if not 1 <= len(seat_ids) <= MAX_SEATS_PER_BOOKING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"select between 1 and {MAX_SEATS_PER_BOOKING} seats",
        )

    requested = set(seat_ids)
    if len(requested) != len(seat_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="duplicate seat ids")

    chosen = db.query(Seat).filter(Seat.id.in_(requested)).all()
    if len(chosen) != len(requested) or any(seat.event_id != event_id for seat in chosen):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="one or more seats do not belong to this event",
        )
    if any(seat.is_blocked for seat in chosen):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more seats are no longer available",
        )

    # Commits on its own, so it has to run before the claim transaction opens.
    release_expired_holds(db, event_id)

    now = _utcnow()
    expires_at = now + timedelta(seconds=get_seat_hold_seconds())
    try:
        # A user gets one active hold per event; taking a new one drops the old.
        previous = (
            db.query(SeatHold)
            .filter(
                SeatHold.event_id == event_id,
                SeatHold.user_id == user.id,
                SeatHold.status == "active",
            )
            .all()
        )
        previous_ids = [hold.id for hold in previous]
        if previous_ids:
            db.query(Seat).filter(Seat.hold_id.in_(previous_ids)).update(
                {Seat.hold_id: None},
                synchronize_session=False,
            )
            for old_hold in previous:
                old_hold.status = "cancelled"

        hold = SeatHold(
            event_id=event_id,
            user_id=user.id,
            status="active",
            created_at=now,
            expires_at=expires_at,
        )
        db.add(hold)
        db.flush()

        # NOT IN over a subquery that contains NULL matches nothing, so the
        # NULL rows have to be filtered out or a fresh event could never be
        # claimed at all.
        booked = select(Ticket.seat_id).where(Ticket.seat_id.isnot(None))
        claimed = (
            db.query(Seat)
            .filter(
                Seat.id.in_(requested),
                Seat.event_id == event_id,
                Seat.hold_id.is_(None),
                Seat.is_blocked.is_(False),
                Seat.id.not_in(booked),
            )
            .update({Seat.hold_id: hold.id}, synchronize_session=False)
        )
        if claimed != len(requested):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more seats are no longer available",
            )

        db.commit()
    except Exception:
        db.rollback()
        raise

    return _hold_out(hold, _hold_seats(db, hold.id), now)


def _owned_hold(db: Session, hold_id: int, user: User) -> SeatHold:
    """A hold the caller owns. Someone else's hold is a 404, not a 403."""
    hold = db.get(SeatHold, hold_id)
    if hold is None or hold.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hold not found")
    return hold


def get_hold(db: Session, hold_id: int, user: User) -> HoldOut:
    hold = _owned_hold(db, hold_id, user)
    now = _utcnow()

    if hold.status == "active" and hold.expires_at <= now:
        release_expired_holds(db, hold.event_id)
        db.refresh(hold)

    seats = _hold_seats(db, hold.id) if hold.status == "active" else []
    return _hold_out(hold, seats, now)


def cancel_hold(db: Session, hold_id: int, user: User) -> HoldOut:
    """Give the seats back. Cancelling an already-cancelled hold is a no-op."""
    hold = _owned_hold(db, hold_id, user)
    now = _utcnow()

    if hold.status == "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="booking already confirmed")
    if hold.status == "active" and hold.expires_at <= now:
        release_expired_holds(db, hold.event_id)
        db.refresh(hold)
    if hold.status != "active":
        return _hold_out(hold, [], now)

    released = _hold_seats(db, hold.id)
    try:
        # Same conditional shape as confirm, so a cancel racing a confirm cannot
        # both succeed: whichever UPDATE matches first wins.
        updated = (
            db.query(SeatHold)
            .filter(
                SeatHold.id == hold.id,
                SeatHold.user_id == user.id,
                SeatHold.status == "active",
            )
            .update({SeatHold.status: "cancelled"}, synchronize_session=False)
        )
        if updated == 1:
            db.query(Seat).filter(Seat.hold_id == hold.id).update(
                {Seat.hold_id: None},
                synchronize_session=False,
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(hold)
    if updated != 1:
        if hold.status == "confirmed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="booking already confirmed")
        return _hold_out(hold, [], now)

    return _hold_out(hold, released, now)


def confirm_hold(
    db: Session,
    hold_id: int,
    user: User,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
) -> ConfirmHoldOut:
    """Turn every held seat into a ticket, once.

    The status change is a conditional UPDATE and the first statement in the
    transaction, so a double click or two simultaneous confirms cannot both
    issue tickets: the loser sees rowcount 0 and rolls back.
    """
    hold = _owned_hold(db, hold_id, user)

    if hold.status == "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already confirmed")
    if hold.status != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="hold expired")

    # Expires anything stale for the event, including this hold, and commits.
    release_expired_holds(db, hold.event_id)
    db.refresh(hold)
    if hold.status == "confirmed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already confirmed")
    if hold.status != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="hold expired")

    now = _utcnow()
    try:
        claimed = (
            db.query(SeatHold)
            .filter(
                SeatHold.id == hold.id,
                SeatHold.user_id == user.id,
                SeatHold.status == "active",
                SeatHold.expires_at > now,
            )
            .update({SeatHold.status: "confirmed"}, synchronize_session=False)
        )
        if claimed != 1:
            raise _HoldNotClaimed

        seats = _hold_seats(db, hold.id)
        attendee = _get_or_create_attendee(
            db,
            attendee_name,
            str(attendee_contact),
            campus_id,
        )
        tickets = [
            _issue_ticket_record(
                db=db,
                event_id=hold.event_id,
                attendee_name=attendee_name,
                attendee_contact=str(attendee_contact),
                campus_id=campus_id,
                tier=seat.tier_name,
                attendee=attendee,
                user_id=user.id,
                seat=seat,
            )
            for seat in seats
        ]
        # The tickets now point at the seats, which is what makes them read as
        # booked, so the ephemeral hold link can go.
        db.query(Seat).filter(Seat.hold_id == hold.id).update(
            {Seat.hold_id: None},
            synchronize_session=False,
        )
        db.commit()
    except _HoldNotClaimed:
        db.rollback()
        fresh = db.get(SeatHold, hold_id)
        if fresh is None or fresh.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hold not found")
        if fresh.status == "confirmed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already confirmed")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="hold expired")
    except Exception:
        db.rollback()
        raise

    db.refresh(hold)
    return ConfirmHoldOut(
        hold_id=hold.id,
        status=hold.status,
        tickets=[_ticket_detail(ticket) for ticket in tickets],
    )


def join_event_waitlist(
    db: Session,
    event_id: int,
    user: User,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
) -> WaitlistEntry:
    """Queue a seat-map attendee, but only once the room is genuinely full."""
    _seat_map_event(db, event_id)
    release_expired_holds(db, event_id)

    existing = (
        db.query(WaitlistEntry)
        .filter(
            WaitlistEntry.event_id == event_id,
            WaitlistEntry.user_id == user.id,
            WaitlistEntry.status == "waiting",
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already on the waitlist")

    if _available_seats(db, event_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="seats are still available")

    entry = join_waitlist(
        db=db,
        event_id=event_id,
        attendee_name=attendee_name,
        attendee_contact=str(attendee_contact),
        campus_id=campus_id,
        # A seat-driven queue does not pick a tier: the seat that frees up does.
        tier="any",
        user_id=user.id,
    )
    if entry.user_id != user.id:
        # join_waitlist is idempotent per attendee, so a second account joining
        # for an attendee already in the queue would otherwise be handed someone
        # else's entry.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this attendee is already on the waitlist",
        )
    return entry


def _issue_seat_ticket(
    db: Session,
    event: Event,
    payload: TicketCreate,
    user_id: int | None,
) -> Ticket | WaitlistEntry:
    """Seat-map counterpart of the legacy auto-numbered issuance path."""
    release_expired_holds(db, event.id)

    tier_names = [
        name
        for (name,) in db.query(Seat.tier_name)
        .filter(Seat.event_id == event.id)
        .order_by(Seat.row_order.asc())
        .distinct()
        .all()
    ]
    requested = payload.tier.strip()
    tier_name = next(
        (name for name in tier_names if name.casefold() == requested.casefold()),
        None,
    )
    if tier_name is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown tier")

    attendee = _get_or_create_attendee(
        db,
        payload.attendee_name,
        str(payload.attendee_contact),
        payload.campus_id,
    )

    # Kept only on this path, as agreed: a hold confirmation issues one ticket
    # per seat for the same attendee and must not be blocked by this.
    if find_active_ticket_for_user(db, event_id=event.id, user_id=user_id, attendee_id=attendee.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already hold a ticket for this event",
        )

    def waitlist() -> WaitlistEntry:
        # Resolve the attendee here rather than reusing the one from the retry
        # loop: a rollback undoes an attendee insert, so that object can be
        # stale by the time this runs.
        return join_waitlist(
            db=db,
            event_id=event.id,
            attendee_name=payload.attendee_name,
            attendee_contact=str(payload.attendee_contact),
            campus_id=payload.campus_id,
            tier="any",
            user_id=user_id,
        )

    candidates = _available_seats(db, event.id, tier_name)
    if not candidates:
        if _available_seats(db, event.id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="tier sold out")
        return waitlist()

    for attempt in range(3):
        candidates = _available_seats(db, event.id, tier_name)
        if not candidates:
            break
        try:
            if attempt:
                # A rollback also undoes the attendee insert from the failed
                # attempt, so it has to be recreated before retrying.
                attendee = _get_or_create_attendee(
                    db,
                    payload.attendee_name,
                    str(payload.attendee_contact),
                    payload.campus_id,
                )
            ticket = _issue_ticket_record(
                db=db,
                event_id=event.id,
                attendee_name=payload.attendee_name,
                attendee_contact=str(payload.attendee_contact),
                campus_id=payload.campus_id,
                tier=tier_name,
                attendee=attendee,
                user_id=user_id,
                seat=candidates[0],
            )
            db.commit()
            db.refresh(ticket)
            return ticket
        except IntegrityError:
            # Someone else took that seat between the scan and the insert.
            db.rollback()

    if not _available_seats(db, event.id, tier_name):
        if _available_seats(db, event.id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="tier sold out")
        return waitlist()

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="One or more seats are no longer available",
    )


def issue_ticket(db: Session, payload: TicketCreate, user_id: int | None = None) -> Ticket | WaitlistEntry:

    event = db.get(Event, payload.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.has_seat_map:
        return _issue_seat_ticket(db, event, payload, user_id)

    tier = payload.tier.strip().lower()
    if tier not in TIER_PREFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose General, Premium, or VIP seating")

    attendee = _get_or_create_attendee(
        db,
        payload.attendee_name,
        str(payload.attendee_contact),
        payload.campus_id,
    )

    # One seat per person per event. Without this a double click, a retry after a
    # dropped connection, or one person submitting twice hands out a second seat
    # for the same attendee, and each of those silently shrinks the event. The
    # waitlist join is idempotent for the same reason. This runs before the
    # capacity check on purpose: a user who already holds a seat must not be
    # dropped into the queue just because the event filled up after they booked.
    if find_active_ticket_for_user(db, event_id=event.id, user_id=user_id, attendee_id=attendee.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already hold a ticket for this event",
        )

    issued_count = db.query(Ticket).filter(Ticket.event_id == event.id, Ticket.status != "revoked").count()
    if issued_count >= event.capacity:
        return join_waitlist(
            db=db,
            event_id=event.id,
            attendee_name=payload.attendee_name,
            attendee_contact=str(payload.attendee_contact),
            campus_id=payload.campus_id,
            tier=tier,
            user_id=user_id,
            attendee=attendee,
        )

    ticket = _issue_ticket_record(
        db=db,
        event_id=event.id,
        attendee_name=payload.attendee_name,
        attendee_contact=str(payload.attendee_contact),
        campus_id=payload.campus_id,
        tier=tier,
        attendee=attendee,
        user_id=user_id,
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def _ticket_detail(ticket: Ticket) -> TicketDetail:
    return TicketDetail(
        ticket_id=ticket.id,
        qr_signature=ticket.qr_signature or "",
        tier=ticket.tier,
        seat_number=ticket.seat_number or "",
        status=ticket.status,
        event=EventOut.model_validate(ticket.event),
        attendee=AttendeeOut.model_validate(ticket.attendee),
    )


def get_my_registrations(db: Session, user: User) -> MyRegistrations:
    """Everything the caller holds, both halves of it.

    This is the read side of ET-07's in-app notice: the frontend polls it to
    show a live position, to surface a ticket that ET-11's revocation handed
    them, and to redraw "my tickets" after a reload. The two lists answer
    different questions and overlap on purpose -- a promoted attendee reaches
    their ticket through the waitlist entry, since ``promote_next_waitlisted``
    issues from queue data that carries no owning account, while a directly
    issued ticket is reachable through ``tickets.user_id``.
    """
    entries = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.user_id == user.id)
        .order_by(WaitlistEntry.created_at.desc(), WaitlistEntry.id.desc())
        .all()
    )

    rows: list[MyWaitlistEntry] = []
    for entry in entries:
        promoted: TicketDetail | None = None
        if entry.promoted_ticket_id is not None:
            ticket = (
                db.query(Ticket)
                .options(joinedload(Ticket.event), joinedload(Ticket.attendee))
                .filter(Ticket.id == entry.promoted_ticket_id)
                .one_or_none()
            )
            if ticket is not None:
                promoted = _ticket_detail(ticket)

        rows.append(
            MyWaitlistEntry(
                waitlist_entry_id=entry.id,
                event_id=entry.event_id,
                event_title=entry.event.title,
                event_venue=entry.event.venue,
                event_date_time=entry.event.date_time,
                attendee_name=entry.attendee_name,
                attendee_contact=entry.attendee_contact,
                campus_id=entry.campus_id or "",
                tier=entry.tier,
                position=entry.position if entry.status == "waiting" else None,
                status=entry.status,
                created_at=entry.created_at,
                promoted_ticket=promoted,
            )
        )

    return MyRegistrations(waitlist=rows, tickets=_my_tickets(db, user))


def _my_tickets(db: Session, user: User) -> list[TicketDetail]:
    """The caller's own live tickets, newest first.

    Revoked rows are left out: they no longer hold a seat, and the admin
    revocation that freed it is what the waitlist handover is for. Tickets
    issued before ``tickets.user_id`` existed have no owner recorded and are
    not recoverable from here.
    """
    tickets = (
        db.query(Ticket)
        .options(joinedload(Ticket.event), joinedload(Ticket.attendee))
        .filter(Ticket.user_id == user.id, Ticket.status != "revoked")
        .order_by(Ticket.issued_at.desc(), Ticket.id.desc())
        .all()
    )
    return [_ticket_detail(ticket) for ticket in tickets]


def get_my_tickets(db: Session, user: User) -> list[TicketDetail]:
    """Everything this account has ever been issued, revoked rows included.

    Unlike ``_my_tickets`` (which backs ``/me/registrations`` and hides revoked
    rows so a freed seat does not look like a live pass), this is the history
    view: the seat label is kept on the revoked row, so the caller can still see
    which seat they gave up and when the status changed.
    """
    tickets = (
        db.query(Ticket)
        .options(joinedload(Ticket.event), joinedload(Ticket.attendee))
        .filter(Ticket.user_id == user.id)
        .order_by(Ticket.issued_at.desc(), Ticket.id.desc())
        .all()
    )
    return [_ticket_detail(ticket) for ticket in tickets]


def get_my_ticket_stats(db: Session, user: User) -> MyTicketStats:
    tickets = (
        db.query(Ticket)
        .options(joinedload(Ticket.event), joinedload(Ticket.attendee))
        .filter(Ticket.user_id == user.id, Ticket.status != "revoked")
        .order_by(Ticket.issued_at.desc(), Ticket.id.desc())
        .all()
    )
    ticket_ids = [ticket.id for ticket in tickets]

    first_valid_scan: dict[int, Scan] = {}
    if ticket_ids:
        valid_scans = (
            db.query(Scan)
            .options(joinedload(Scan.gate))
            .filter(Scan.ticket_id.in_(ticket_ids), Scan.result == "valid")
            .order_by(Scan.timestamp.asc())
            .all()
        )
        for scan in valid_scans:
            if scan.ticket_id not in first_valid_scan:
                first_valid_scan[scan.ticket_id] = scan

    tier_counts: dict[str, int] = {}
    rows: list[MyTicketStatItem] = []
    used = 0
    for ticket in tickets:
        tier_key = (ticket.tier or "general").lower()
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1

        scan = first_valid_scan.get(ticket.id)
        is_used = ticket.status == "used" or scan is not None
        if is_used:
            used += 1

        rows.append(
            MyTicketStatItem(
                ticket_id=ticket.id,
                event_title=ticket.event.title if ticket.event else "",
                event_date_time=ticket.event.date_time if ticket.event else ticket.issued_at,
                venue=ticket.event.venue if ticket.event else "",
                tier=ticket.tier,
                seat_number=ticket.seat_number or "",
                status=ticket.status,
                checked_in=is_used,
                check_gate_name=scan.gate.name if scan and scan.gate else None,
                checked_at=scan.timestamp if scan else None,
            )
        )

    waitlisted = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.user_id == user.id, WaitlistEntry.status == "waiting")
        .count()
    )

    return MyTicketStats(
        total=len(tickets),
        used=used,
        valid=len(tickets) - used,
        waitlisted=waitlisted,
        tiers=[MyTicketTier(tier=tier, count=count) for tier, count in sorted(tier_counts.items())],
        tickets=rows,
    )


def get_my_scan_stats(db: Session, user: User) -> MyScanStats:
    scans = (
        db.query(Scan)
        .options(
            joinedload(Scan.ticket).joinedload(Ticket.attendee),
            joinedload(Scan.gate),
            joinedload(Scan.ticket).joinedload(Ticket.event),
        )
        .filter(Scan.user_id == user.id)
        .order_by(Scan.timestamp.desc(), Scan.id.desc())
        .all()
    )

    valid = sum(1 for scan in scans if scan.result == "valid")
    duplicate = sum(1 for scan in scans if scan.result == "duplicate")
    invalid = sum(1 for scan in scans if scan.result not in ("valid", "duplicate"))

    gate_counts: dict[int, dict] = {}
    for scan in scans:
        if scan.result != "valid":
            continue
        entry = gate_counts.get(scan.gate_id)
        if entry is None:
            gate_counts[scan.gate_id] = {
                "gate_id": scan.gate_id,
                "name": scan.gate.name if scan.gate else "",
                "location": scan.gate.location if scan.gate else "",
                "scanned_count": 1,
            }
        else:
            entry["scanned_count"] += 1

    recent = [
        MyScanItem(
            scan_id=scan.id,
            timestamp=scan.timestamp,
            result=scan.result,
            ticket_id=scan.ticket_id,
            attendee_name=scan.ticket.attendee.name if scan.ticket and scan.ticket.attendee else "",
            tier=scan.ticket.tier if scan.ticket else "",
            seat_number=scan.ticket.seat_number if scan.ticket else "",
            gate_name=scan.gate.name if scan.gate else "",
            gate_location=scan.gate.location if scan.gate else "",
            event_title=scan.ticket.event.title if scan.ticket and scan.ticket.event else "",
        )
        for scan in scans[:25]
    ]

    return MyScanStats(
        total=len(scans),
        valid=valid,
        duplicate=duplicate,
        invalid=invalid,
        gates=[MyScanGate(**entry) for entry in sorted(gate_counts.values(), key=lambda item: item["name"])],
        recent=recent,
    )


def revoke_ticket(db: Session, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if ticket.status == "revoked":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket already revoked")

    try:
        ticket.status = "revoked"
        ticket.revoked_at = _utcnow()

        # Clear the seat reference first: it is UNIQUE, and the promoted ticket
        # must be able to claim the seat before this transaction ends.
        freed_seat: Seat | None = None
        if ticket.seat_id is not None:
            freed_seat = db.get(Seat, ticket.seat_id)
            ticket.seat_id = None
        db.flush()

        if freed_seat is not None:
            # Seat-map event: the queue entry inherits this exact seat.
            promoted_info = promote_next_waitlisted(db=db, event_id=ticket.event_id, seat=freed_seat)
        elif not ticket.event.has_seat_map:
            # One seat freed, so exactly one promotion. See promote_next_waitlisted's
            # contract for other callers.
            promoted_info = promote_next_waitlisted(db=db, event_id=ticket.event_id)
        else:
            # A seat-map ticket with no seat pointer freed nothing, so there is
            # nothing to hand to the queue.
            promoted_info = None

        db.commit()
        db.refresh(ticket)
        ticket.promoted_attendee = promoted_info
        return ticket
    except Exception:
        db.rollback()
        raise



def find_ticket_by_scan_payload(db: Session, payload: ScanCreate) -> Ticket | None:
    ticket_id = payload.ticket_id
    if payload.qr_signature:
        signed_ticket_id = ticket_id_from_signature(payload.qr_signature)
        if signed_ticket_id is None:
            return None
        ticket_id = signed_ticket_id

    if ticket_id is None:
        return None

    return (
        db.query(Ticket)
        .options(joinedload(Ticket.attendee), joinedload(Ticket.scans).joinedload(Scan.gate))
        .filter(Ticket.id == ticket_id)
        .one_or_none()
    )


def record_scan(db: Session, payload: ScanCreate, user_id: int | None = None) -> ScanResult:
    gate = db.get(Gate, payload.gate_id)
    volunteer = db.get(Volunteer, payload.volunteer_id)

    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
    if volunteer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Volunteer not found")
    if volunteer.gate_id is not None and volunteer.gate_id != gate.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Volunteer is not assigned to this gate")

    ticket = find_ticket_by_scan_payload(db, payload)
    if ticket is None:
        if payload.ticket_id is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        return ScanResult(result="invalid", message="QR signature could not be verified.")

    if gate.event_id is not None and ticket.event_id != gate.event_id:
        scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid", user_id=user_id)
        db.add(scan)
        db.commit()
        db.refresh(scan)
        return ScanResult(
            result="invalid",
            message="Ticket does not belong to this gate's event.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
            scanned_at=scan.timestamp,
        )

    if ticket.status == "revoked":
        scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid", user_id=user_id)
        db.add(scan)
        db.commit()
        db.refresh(scan)
        return ScanResult(
            result="invalid",
            message="Ticket has been revoked.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
            scanned_at=scan.timestamp,
        )

    prior_valid_scan = (
        db.query(Scan)
        .options(joinedload(Scan.gate))
        .filter(Scan.ticket_id == ticket.id, Scan.result == "valid")
        .order_by(Scan.timestamp.asc())
        .first()
    )
    if prior_valid_scan is not None:
        duplicate = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="duplicate", user_id=user_id)
        db.add(duplicate)
        db.commit()
        db.refresh(duplicate)
        return ScanResult(
            result="duplicate",
            message=f"Already used at {prior_valid_scan.gate.name}.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
            scanned_at=duplicate.timestamp,
            prior_scan=PriorScan(gate_name=prior_valid_scan.gate.name, timestamp=prior_valid_scan.timestamp),
        )

    ticket.status = "used"
    scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="valid", user_id=user_id)
    db.add(scan)
    db.commit()
    db.refresh(scan)
    return ScanResult(
        result="valid",
        message="Ticket accepted. Welcome in.",
        ticket_id=ticket.id,
        attendee_name=ticket.attendee.name,
        tier=ticket.tier,
        seat_number=ticket.seat_number,
        scanned_at=scan.timestamp,
    )


def create_gate(db: Session, payload: GateCreate) -> Gate:
    event = db.get(Event, payload.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    gate = Gate(name=payload.name.strip(), location=payload.location.strip(), event_id=event.id)
    db.add(gate)
    db.flush()

    if payload.volunteer_name:
        db.add(Volunteer(name=payload.volunteer_name.strip(), gate_id=gate.id))

    db.commit()
    db.refresh(gate)
    return gate


def get_gate_status(db: Session, event_id: int | None = None) -> list[GateStatus]:
    query = db.query(
        Gate.id,
        Gate.event_id,
        Gate.name,
        Gate.location,
        func.count(Scan.id).filter(Scan.result == "valid").label("scanned_count"),
        func.max(Scan.timestamp).label("last_synced"),
    ).outerjoin(Scan)

    if event_id is not None:
        query = query.filter(Gate.event_id == event_id)

    rows = query.group_by(Gate.id, Gate.event_id, Gate.name, Gate.location).order_by(Gate.id).all()

    return [
        GateStatus(
            gate_id=row.id,
            event_id=row.event_id,
            name=row.name,
            location=row.location,
            online=True,
            scanned_count=row.scanned_count or 0,
            last_synced=row.last_synced if isinstance(row.last_synced, datetime) else None,
        )
        for row in rows
    ]


def get_event_stats(db: Session, event_id: int) -> EventStatsOut:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    tickets = (
        db.query(Ticket)
        .options(joinedload(Ticket.attendee))
        .filter(Ticket.event_id == event_id)
        .order_by(Ticket.id.asc())
        .all()
    )
    ticket_ids = [ticket.id for ticket in tickets]

    first_valid_scan: dict[int, Scan] = {}
    if ticket_ids:
        valid_scans = (
            db.query(Scan)
            .options(joinedload(Scan.gate), joinedload(Scan.volunteer))
            .filter(Scan.ticket_id.in_(ticket_ids), Scan.result == "valid")
            .order_by(Scan.timestamp.asc())
            .all()
        )
        for scan in valid_scans:
            if scan.ticket_id not in first_valid_scan:
                first_valid_scan[scan.ticket_id] = scan

    gates = db.query(Gate).filter(Gate.event_id == event_id).order_by(Gate.id.asc()).all()
    gate_counts: dict[int, int] = {gate.id: 0 for gate in gates}
    for scan in first_valid_scan.values():
        if scan.gate_id in gate_counts:
            gate_counts[scan.gate_id] += 1

    tier_issued: dict[str, int] = {}
    tier_checked: dict[str, int] = {}
    ticket_rows: list[EventStatsTicket] = []
    for ticket in tickets:
        # Seat-map tiers are admin-defined ("Balcony", "Standing"), so the name
        # is kept exactly as the layout spelt it. Legacy keeps its lowercased
        # General/Premium/VIP keys.
        tier_key = ticket.tier if event.has_seat_map else (ticket.tier or "general").lower()
        if ticket.status != "revoked":
            tier_issued[tier_key] = tier_issued.get(tier_key, 0) + 1

        scan = first_valid_scan.get(ticket.id)
        is_checked = scan is not None
        if is_checked and ticket.status != "revoked":
            tier_checked[tier_key] = tier_checked.get(tier_key, 0) + 1

        attendee = ticket.attendee
        ticket_rows.append(
            EventStatsTicket(
                ticket_id=ticket.id,
                attendee_name=attendee.name if attendee else "",
                campus_id=attendee.campus_id if attendee else "",
                contact_email=attendee.contact_email if attendee else "",
                tier=ticket.tier,
                seat_number=ticket.seat_number or "",
                status=ticket.status,
                issued_at=ticket.issued_at,
                checked_in=is_checked,
                check_gate_name=scan.gate.name if scan and scan.gate else None,
                check_gate_location=scan.gate.location if scan and scan.gate else None,
                checked_at=scan.timestamp if scan else None,
                checked_by=scan.volunteer.name if scan and scan.volunteer else None,
            )
        )

    active_tickets = [ticket for ticket in tickets if ticket.status != "revoked"]
    issued = len(active_tickets)
    checked_in = len([ticket for ticket in active_tickets if ticket.status == "used" or ticket.id in first_valid_scan])
    remaining = max(event.capacity - issued, 0)
    check_in_rate = round((checked_in / issued * 100) if issued else 0.0, 1)

    if event.has_seat_map:
        # Show every tier the layout defines, in layout order, even when nothing
        # has been sold in it yet -- an empty "Balcony" row is information.
        seat_tiers = [
            name
            for (name,) in db.query(Seat.tier_name)
            .filter(Seat.event_id == event_id)
            .order_by(Seat.row_order.asc())
            .distinct()
            .all()
        ]
        for tier in dict.fromkeys(seat_tiers):
            tier_issued.setdefault(tier, 0)
            tier_checked.setdefault(tier, 0)
        tier_order = list(dict.fromkeys(seat_tiers))
    else:
        tier_order = sorted(tier_issued)

    tier_breakdown = [
        EventStatsTier(tier=tier, issued=tier_issued.get(tier, 0), checked_in=tier_checked.get(tier, 0))
        for tier in tier_order
    ]
    gate_breakdown = [
        EventStatsGate(
            gate_id=gate.id,
            name=gate.name,
            location=gate.location,
            scanned_count=gate_counts.get(gate.id, 0),
        )
        for gate in gates
    ]

    waitlist_entries = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
        .order_by(WaitlistEntry.position.asc())
        .all()
    )
    waitlist_rows = [
        WaitlistEntryOut(
            id=entry.id,
            event_id=entry.event_id,
            attendee_name=entry.attendee_name,
            attendee_contact=entry.attendee_contact,
            campus_id=entry.campus_id,
            tier=entry.tier,
            position=entry.position,
            status=entry.status,
            created_at=entry.created_at,
        )
        for entry in waitlist_entries
    ]

    # Recently promoted, so an admin revoking a seat can confirm the next person
    # in line actually received it rather than the seat silently vanishing.
    promoted_entries = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "promoted")
        .order_by(WaitlistEntry.resolved_at.desc(), WaitlistEntry.id.desc())
        .limit(5)
        .all()
    )
    promoted_rows = [
        WaitlistEntryOut(
            id=entry.id,
            event_id=entry.event_id,
            attendee_name=entry.attendee_name,
            attendee_contact=entry.attendee_contact,
            campus_id=entry.campus_id,
            tier=entry.tier,
            position=entry.position,
            status=entry.status,
            created_at=entry.created_at,
            promoted_ticket_id=entry.promoted_ticket_id,
            resolved_at=entry.resolved_at,
        )
        for entry in promoted_entries
    ]

    return EventStatsOut(
        event_id=event.id,
        title=event.title,
        venue=event.venue,
        date_time=event.date_time,
        capacity=event.capacity,
        issued=issued,
        checked_in=checked_in,
        remaining=remaining,
        check_in_rate=check_in_rate,
        tier_breakdown=tier_breakdown,
        gate_breakdown=gate_breakdown,
        tickets=ticket_rows,
        waitlist=waitlist_rows,
        promoted=promoted_rows,
    )


def build_attendance_csv(db: Session, event_id: int) -> str:
    stats = get_event_stats(db, event_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Attendee Name",
        "Contact Email",
        "Campus ID",
        "Tier",
        "Seat Number",
        "Status",
        "Gate",
        "Check-in Timestamp",
    ])

    for ticket in stats.tickets:
        if ticket.status == "used":
            export_status = "Checked in"
            gate = ticket.check_gate_name or ""
            check_in_timestamp = (
                ticket.checked_at.isoformat()
                if ticket.checked_at and hasattr(ticket.checked_at, "isoformat")
                else str(ticket.checked_at) if ticket.checked_at else ""
            )
        elif ticket.status == "issued":
            export_status = "No-show"
            gate = ""
            check_in_timestamp = ""
        elif ticket.status == "revoked":
            export_status = "Revoked"
            gate = ""
            check_in_timestamp = ""
        else:
            export_status = ticket.status
            gate = ""
            check_in_timestamp = ""

        writer.writerow([
            ticket.attendee_name or "",
            ticket.contact_email or "",
            ticket.campus_id or "",
            ticket.tier or "",
            ticket.seat_number or "",
            export_status,
            gate,
            check_in_timestamp,
        ])

    total_issued = sum(1 for t in stats.tickets if t.status != "revoked")
    checked_in = sum(1 for t in stats.tickets if t.status == "used")
    no_shows = sum(1 for t in stats.tickets if t.status == "issued")
    revoked = sum(1 for t in stats.tickets if t.status == "revoked")
    rate_val = round((checked_in / total_issued * 100) if total_issued else 0.0, 1)
    rate_str = f"{int(rate_val)}%" if rate_val.is_integer() else f"{rate_val}%"

    writer.writerow([])
    writer.writerow(["Summary", "", "", "", "", "", "", ""])
    writer.writerow(["Total Tickets Issued", str(total_issued), "", "", "", "", "", ""])
    writer.writerow(["Checked In", str(checked_in), "", "", "", "", "", ""])
    writer.writerow(["No-Shows", str(no_shows), "", "", "", "", "", ""])
    writer.writerow(["Revoked", str(revoked), "", "", "", "", "", ""])
    writer.writerow(["Check-in Rate", rate_str, "", "", "", "", "", ""])

    return output.getvalue()


def _username_from_email(db: Session, email: str, sub: str) -> str:
    local = email.split("@")[0].lower() if "@" in email else email.lower()
    cleaned = "".join(ch for ch in local if ch.isalnum() or ch in ("_", ".", "-")).strip("._-")
    if len(cleaned) < 3:
        cleaned = f"user_{sub[:6].lower()}"
    base = cleaned[:70]
    candidate = base
    suffix = 1
    while db.query(User).filter(User.username == candidate).first():
        tail = f"_{sub[:6].lower()}" if suffix == 1 else f"_{suffix}"
        candidate = f"{base[: 80 - len(tail)]}{tail}"
        suffix += 1
        if suffix > 20:
            candidate = f"{base[:60]}_{sub[:12].lower()}"
            break
    return candidate[:80]


def get_or_create_google_user(db: Session, google_info: dict, role: str = "user") -> User:
    if role not in ("user", "scanner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google login is not allowed for this role")

    sub = str(google_info.get("sub", "")).strip()
    email = str(google_info.get("email", "")).strip().lower()
    name = str(google_info.get("name", "")).strip()
    if not sub or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google login")

    existing = db.query(User).filter(User.google_sub == sub).one_or_none()
    if existing:
        if existing.role != role and existing.role in ("user", "scanner"):
            existing.role = role
            db.commit()
            db.refresh(existing)
        return existing

    linked = (
        db.query(User)
        .filter((User.email == email) | (User.username == email))
        .order_by(User.id.asc())
        .first()
    )
    if linked:
        if linked.google_sub and linked.google_sub != sub:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email is already linked to another Google account")
        linked.google_sub = sub
        if not linked.email:
            linked.email = email
        if linked.role != role and linked.role in ("user", "scanner"):
            linked.role = role
        db.commit()
        db.refresh(linked)
        return linked

    username = _username_from_email(db, email, sub)
    display_name = (name or email.split("@")[0])[:120].strip() or username
    user = User(
        username=username,
        password_hash="",
        role=role,
        display_name=display_name,
        google_sub=sub,
        email=email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
