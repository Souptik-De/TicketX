from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .models import Attendee, Event, Gate, Ticket, Volunteer
from .schemas import GateCreate, TicketCreate
from .security import sign_ticket


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
