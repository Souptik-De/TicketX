from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload, subqueryload

from .auth import create_token, hash_password, require_roles, verify_password
from .database import SessionLocal, create_database, get_db
from .models import Event, Gate, Scan, Ticket, User, Volunteer
from .schemas import (
    EventCreate,
    EventOut,
    GateCreate,
    GateOut,
    GateStatus,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    ScanCreate,
    ScanResult,
    TicketCreate,
    TicketCreated,
    TicketDetail,
    UserOut,
    VolunteerOut,
)
from .seed import seed_reference_data
from .services import create_gate, get_gate_status, issue_ticket, record_scan


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    create_database()
    with SessionLocal() as db:
        seed_reference_data(db)
    yield


app = FastAPI(title="TicketX API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.username == payload.username.strip().lower()).one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    return LoginResponse(token=create_token(user), user=UserOut.model_validate(user))


@app.post("/auth/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> LoginResponse:
    if payload.role == "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin registration is not allowed")
    username = payload.username.strip().lower()
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        role=payload.role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return LoginResponse(token=create_token(user), user=UserOut.model_validate(user))


@app.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(require_roles("user", "admin", "scanner"))) -> User:
    return current_user


@app.get("/events", response_model=list[EventOut])
def list_events(db: Session = Depends(get_db)) -> list[Event]:
    return db.query(Event).options(subqueryload(Event.tickets)).order_by(Event.date_time.asc()).all()


@app.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> Event:
    event = Event(
        title=payload.title.strip(),
        description=payload.description.strip(),
        date_time=payload.date_time,
        venue=payload.venue.strip(),
        capacity=payload.capacity,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@app.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: int, _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Delete scans for tickets belonging to this event
    ticket_ids = db.query(Ticket.id).filter(Ticket.event_id == event_id).subquery()
    db.query(Scan).filter(Scan.ticket_id.in_(ticket_ids)).delete(synchronize_session=False)

    # Delete tickets for this event
    db.query(Ticket).filter(Ticket.event_id == event_id).delete(synchronize_session=False)

    # Delete scans performed by volunteers assigned to gates for this event
    gate_ids = db.query(Gate.id).filter(Gate.event_id == event_id).subquery()
    volunteer_ids = db.query(Volunteer.id).filter(Volunteer.gate_id.in_(gate_ids)).subquery()
    db.query(Scan).filter(Scan.volunteer_id.in_(volunteer_ids)).delete(synchronize_session=False)

    # Delete volunteers assigned to gates for this event
    db.query(Volunteer).filter(Volunteer.gate_id.in_(gate_ids)).delete(synchronize_session=False)

    # Delete gates for this event
    db.query(Gate).filter(Gate.event_id == event_id).delete(synchronize_session=False)

    db.delete(event)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/volunteers", response_model=list[VolunteerOut])
def list_volunteers(_: User = Depends(require_roles("admin", "scanner")), db: Session = Depends(get_db)) -> list[Volunteer]:
    return db.query(Volunteer).order_by(Volunteer.id.asc()).all()


@app.get("/gates", response_model=list[GateOut])
def list_gates(
    event_id: int | None = Query(default=None),
    _: User = Depends(require_roles("admin", "scanner")),
    db: Session = Depends(get_db),
) -> list[Gate]:
    query = db.query(Gate)
    if event_id is not None:
        query = query.filter(Gate.event_id == event_id)
    return query.order_by(Gate.id.asc()).all()


@app.post("/gates", response_model=GateOut, status_code=status.HTTP_201_CREATED)
def add_gate(payload: GateCreate, _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> Gate:
    return create_gate(db, payload)


@app.post("/tickets", response_model=TicketCreated, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    response: Response,
    _: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> TicketCreated:
    ticket = issue_ticket(db, payload)
    response.headers["Location"] = f"/tickets/{ticket.id}"
    return TicketCreated(
        ticket_id=ticket.id,
        qr_signature=ticket.qr_signature or "",
        tier=ticket.tier,
        seat_number=ticket.seat_number or "",
        status=ticket.status,
    )


@app.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(
    ticket_id: int,
    _: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> TicketDetail:
    ticket = (
        db.query(Ticket)
        .options(joinedload(Ticket.event), joinedload(Ticket.attendee))
        .filter(Ticket.id == ticket_id)
        .one_or_none()
    )
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    return TicketDetail(
        ticket_id=ticket.id,
        qr_signature=ticket.qr_signature or "",
        tier=ticket.tier,
        seat_number=ticket.seat_number or "",
        status=ticket.status,
        event=ticket.event,
        attendee=ticket.attendee,
    )


@app.post("/scans", response_model=ScanResult)
def scan_ticket(
    payload: ScanCreate,
    _: User = Depends(require_roles("scanner")),
    db: Session = Depends(get_db),
) -> ScanResult:
    return record_scan(db, payload)


@app.get("/gates/status", response_model=list[GateStatus])
def gates_status(
    event_id: int | None = Query(default=None),
    _: User = Depends(require_roles("admin", "scanner")),
    db: Session = Depends(get_db),
) -> list[GateStatus]:
    return get_gate_status(db, event_id=event_id)
