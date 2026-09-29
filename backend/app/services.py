import csv
from datetime import UTC, datetime
import io

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from .models import Attendee, Event, Gate, Scan, Ticket, User, Volunteer, WaitlistEntry
from .schemas import (
    AttendeeOut,
    EventOut,
    EventStatsGate,
    EventStatsOut,
    EventStatsTicket,
    EventStatsTier,
    GateCreate,
    GateStatus,
    MyRegistrations,
    MyWaitlistEntry,
    PriorScan,
    PromotedAttendeeInfo,
    ScanCreate,
    ScanResult,
    TicketCreate,
    TicketDetail,
    WaitlistEntryOut,
)
from .security import sign_ticket, ticket_id_from_signature


TIER_PREFIXES = {"general": "GEN", "premium": "PRE", "vip": "VIP"}


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
) -> Ticket:
    tier = tier.strip().lower()
    if tier not in TIER_PREFIXES:
        tier = "general"

    seat_number = _next_seat_number(db, event_id, tier)

    if attendee is None:
        attendee = _get_or_create_attendee(db, attendee_name, attendee_contact, campus_id)

    ticket = Ticket(
        event_id=event_id,
        attendee_id=attendee.id,
        tier=tier,
        seat_number=seat_number,
        status="issued",
        user_id=user_id,
    )
    db.add(ticket)
    db.flush()
    ticket.qr_signature = sign_ticket(ticket.id)
    return ticket


def promote_next_waitlisted(db: Session, event_id: int) -> PromotedAttendeeInfo | None:
    """Hand one freed seat to the person at the head of the queue.

    ET-07 / ET-11 contract for any path that frees a seat:

    * Call this exactly ONCE per seat freed. It does not re-check capacity,
      because the caller has already decided a seat is free.
    * Call it inside the same transaction as the change that freed the seat, and
      let the caller own the commit. On failure the caller's rollback undoes
      both the revocation and the promotion together.
    * A bulk revocation must therefore loop rather than call this once.

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
        tier=entry.tier,
        attendee=entry.attendee,
        # The queue row remembers who asked, so the promoted ticket lands in that
        # account's "my tickets" too rather than only being reachable through the
        # waitlist entry.
        user_id=entry.user_id,
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


def issue_ticket(db: Session, payload: TicketCreate, user_id: int | None = None) -> Ticket | WaitlistEntry:
    event = db.get(Event, payload.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

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


def revoke_ticket(db: Session, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if ticket.status == "revoked":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ticket already revoked")

    try:
        ticket.status = "revoked"
        ticket.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.flush()

        # One seat freed, so exactly one promotion. See promote_next_waitlist's
        # contract for other callers.
        promoted_info = promote_next_waitlisted(db=db, event_id=ticket.event_id)

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


def record_scan(db: Session, payload: ScanCreate) -> ScanResult:
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
        scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid")
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
        scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid")
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
        duplicate = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="duplicate")
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
    scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="valid")
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
        tier_key = (ticket.tier or "general").lower()
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

    tier_breakdown = [
        EventStatsTier(tier=tier, issued=count, checked_in=tier_checked.get(tier, 0))
        for tier, count in sorted(tier_issued.items())
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
