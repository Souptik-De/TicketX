import csv
from datetime import UTC, datetime
import io

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from .models import Attendee, Event, Gate, Scan, Ticket, User, Volunteer, WaitlistEntry
from .schemas import (
    EventStatsGate,
    EventStatsOut,
    EventStatsTicket,
    EventStatsTier,
    GateCreate,
    GateStatus,
    PriorScan,
    PromotedAttendeeInfo,
    ScanCreate,
    ScanResult,
    TicketCreate,
    WaitlistEntryOut,
)
from .security import sign_ticket, ticket_id_from_signature


TIER_PREFIXES = {"general": "GEN", "premium": "PRE", "vip": "VIP"}


def join_waitlist(
    db: Session,
    event_id: int,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
    tier: str,
) -> WaitlistEntry:
    position = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
        .count()
    ) + 1
    entry = WaitlistEntry(
        event_id=event_id,
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


def _issue_ticket_record(
    db: Session,
    event_id: int,
    attendee_name: str,
    attendee_contact: str,
    campus_id: str | None,
    tier: str,
) -> Ticket:
    tier = tier.strip().lower()
    if tier not in TIER_PREFIXES:
        tier = "general"

    tier_issued_count = db.query(Ticket).filter(Ticket.event_id == event_id, Ticket.tier == tier).count()
    seat_number = f"{TIER_PREFIXES[tier]}-{tier_issued_count + 1:03d}"

    clean_campus_id = campus_id.strip() if campus_id else str(attendee_contact).lower()
    attendee = db.query(Attendee).filter(Attendee.campus_id == clean_campus_id).one_or_none()
    if attendee is None:
        attendee = Attendee(
            name=attendee_name.strip(),
            campus_id=clean_campus_id,
            contact_email=str(attendee_contact).lower(),
        )
        db.add(attendee)
        db.flush()

    ticket = Ticket(
        event_id=event_id,
        attendee_id=attendee.id,
        tier=tier,
        seat_number=seat_number,
        status="issued",
    )
    db.add(ticket)
    db.flush()
    ticket.qr_signature = sign_ticket(ticket.id)
    return ticket


def issue_ticket(db: Session, payload: TicketCreate) -> Ticket | WaitlistEntry:
    event = db.get(Event, payload.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    tier = payload.tier.strip().lower()
    if tier not in TIER_PREFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose General, Premium, or VIP seating")

    issued_count = db.query(Ticket).filter(Ticket.event_id == event.id, Ticket.status != "revoked").count()
    if issued_count >= event.capacity:
        return join_waitlist(
            db=db,
            event_id=event.id,
            attendee_name=payload.attendee_name,
            attendee_contact=str(payload.attendee_contact),
            campus_id=payload.campus_id,
            tier=tier,
        )

    ticket = _issue_ticket_record(
        db=db,
        event_id=event.id,
        attendee_name=payload.attendee_name,
        attendee_contact=str(payload.attendee_contact),
        campus_id=payload.campus_id,
        tier=tier,
    )
    db.commit()
    db.refresh(ticket)
    return ticket


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

        event_id = ticket.event_id
        waitlist_entry = (
            db.query(WaitlistEntry)
            .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
            .order_by(WaitlistEntry.position.asc())
            .first()
        )

        promoted_info: PromotedAttendeeInfo | None = None
        if waitlist_entry is not None:
            old_position = waitlist_entry.position

            new_ticket = _issue_ticket_record(
                db=db,
                event_id=event_id,
                attendee_name=waitlist_entry.attendee_name,
                attendee_contact=waitlist_entry.attendee_contact,
                campus_id=waitlist_entry.campus_id,
                tier=waitlist_entry.tier,
            )

            waitlist_entry.status = "promoted"
            db.flush()

            remaining_waiting = (
                db.query(WaitlistEntry)
                .filter(
                    WaitlistEntry.event_id == event_id,
                    WaitlistEntry.status == "waiting",
                    WaitlistEntry.position > old_position,
                )
                .order_by(WaitlistEntry.position.asc())
                .all()
            )
            for entry in remaining_waiting:
                entry.position -= 1
            db.flush()

            promoted_info = PromotedAttendeeInfo(
                attendee_name=waitlist_entry.attendee_name,
                new_ticket_id=new_ticket.id,
                tier=new_ticket.tier,
            )

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
        return ScanResult(
            result="invalid",
            message="Ticket does not belong to this gate's event.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
        )

    if ticket.status == "revoked":
        scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid")
        db.add(scan)
        db.commit()
        return ScanResult(
            result="invalid",
            message="Ticket has been revoked.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
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
        return ScanResult(
            result="duplicate",
            message=f"Already used at {prior_valid_scan.gate.name}.",
            ticket_id=ticket.id,
            attendee_name=ticket.attendee.name,
            tier=ticket.tier,
            seat_number=ticket.seat_number,
            prior_scan=PriorScan(gate_name=prior_valid_scan.gate.name, timestamp=prior_valid_scan.timestamp),
        )

    ticket.status = "used"
    scan = Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="valid")
    db.add(scan)
    db.commit()
    return ScanResult(
        result="valid",
        message="Ticket accepted. Welcome in.",
        ticket_id=ticket.id,
        attendee_name=ticket.attendee.name,
        tier=ticket.tier,
        seat_number=ticket.seat_number,
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
        tier_issued[tier_key] = tier_issued.get(tier_key, 0) + 1

        scan = first_valid_scan.get(ticket.id)
        is_checked = scan is not None
        if is_checked:
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

    issued = len(tickets)
    checked_in = len(first_valid_scan)
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
