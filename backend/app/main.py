from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .auth import create_token, require_roles, verify_password
from .database import SessionLocal, create_database, get_db
from .models import Event, Gate, User, Volunteer
from .schemas import (
    EventCreate,
    EventOut,
    GateCreate,
    GateOut,
    LoginRequest,
    LoginResponse,
    UserOut,
    VolunteerOut,
)
from .seed import seed_reference_data


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


@app.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(require_roles("user", "admin", "scanner"))) -> User:
    return current_user


@app.get("/events", response_model=list[EventOut])
def list_events(_: User = Depends(require_roles("user", "admin", "scanner")), db: Session = Depends(get_db)) -> list[Event]:
    return db.query(Event).order_by(Event.date_time.asc()).all()


@app.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> Event:
    event = Event(
        title=payload.title.strip(),
        date_time=payload.date_time,
        venue=payload.venue.strip(),
        capacity=payload.capacity,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


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
