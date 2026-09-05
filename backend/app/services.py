from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .models import Attendee, Event, Gate, Scan, Ticket, Volunteer
from .schemas import GateCreate, ScanCreate, ScanResult, TicketCreate
from .security import sign_ticket, ticket_id_from_signature


TIER_PREFIXES = {"general": "GEN", "premium": "PRE", "vip": "VIP"}


def issue_ticket(db: Session, payload: TicketCreate) -> Ticket:
    event = db.get(Event, payload.event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    issued_count = db.query(Ticket).filter(Ticket.event_id == event.id).count()
    if issued_count >= event.capacity:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is at capacity")

    tier = payload.tier.strip().lower()
    if tier not in TIER_PREFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose General, Premium, or VIP seating")

    tier_issued_count = db.query(Ticket).filter(Ticket.event_id == event.id, Ticket.tier == tier).count()
    seat_number = f"{TIER_PREFIXES[tier]}-{tier_issued_count + 1:03d}"

    campus_id = payload.campus_id or payload.attendee_contact.lower()
    attendee = db.query(Attendee).filter(Attendee.campus_id == campus_id).one_or_none()
    if attendee is None:
        attendee = Attendee(
            name=payload.attendee_name.strip(),
            campus_id=campus_id,
            contact_email=str(payload.attendee_contact).lower(),
        )
        db.add(attendee)
        db.flush()

    ticket = Ticket(
        event_id=event.id,
        attendee_id=attendee.id,
        tier=tier,
        seat_number=seat_number,
        status="issued",
    )
    db.add(ticket)
    db.flush()
    ticket.qr_signature = sign_ticket(ticket.id)
    db.commit()
    db.refresh(ticket)
    return ticket


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
        .options(joinedload(Ticket.attendee))
        .filter(Ticket.id == ticket_id)
        .one_or_none()
    )


def _invalid_scan_result(db: Session, ticket: Ticket, gate: Gate, volunteer: Volunteer, message: str) -> ScanResult:
    db.add(Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="invalid"))
    db.commit()
    return ScanResult(
        result="invalid",
        message=message,
        ticket_id=ticket.id,
        attendee_name=ticket.attendee.name,
        tier=ticket.tier,
        seat_number=ticket.seat_number,
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
        if payload.ticket_id is not None and not payload.qr_signature:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        return ScanResult(result="invalid", message="QR signature could not be verified.")

    if gate.event_id is not None and ticket.event_id != gate.event_id:
        return _invalid_scan_result(db, ticket, gate, volunteer, "Ticket does not belong to this gate's event.")

    if ticket.status != "issued":
        message = "Ticket has been revoked." if ticket.status == "revoked" else "Ticket is not available for entry."
        return _invalid_scan_result(db, ticket, gate, volunteer, message)

    ticket.status = "used"
    db.add(Scan(ticket_id=ticket.id, gate_id=gate.id, volunteer_id=volunteer.id, result="valid"))
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
