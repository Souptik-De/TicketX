from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import hashlib
import io
import logging

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload, subqueryload

logger = logging.getLogger(__name__)

from .auth import create_token, hash_password, require_roles, verify_password
from .config import demo_seed_enabled
from .database import SessionLocal, create_database, get_db
from .models import Event, Gate, Scan, Ticket, User, Volunteer, WaitlistEntry
from .schemas import (
    EventCreate,
    EventOut,
    EventStatsOut,
    EventSuggestion,
    EventSuggestionOut,
    GateCreate,
    GateOut,
    GateStatus,
    GoogleLoginRequest,
    LoginRequest,
    LoginResponse,
    MyRegistrations,
    MyScanStats,
    MyTicketStats,
    RegisterRequest,
    ScanCreate,
    ScanResult,
    TicketCreate,
    TicketCreated,
    TicketDetail,
    TicketIssuanceResponse,
    WaitlistJoinResponse,
    RevokeTicketResponse,
    UserOut,
    VolunteerOut,
    WaitlistEntryOut,
)
from .seed import seed_demo_data, seed_reference_data
from . import groq
from .recommend import rank_events, signal_label
from .services import (
    build_attendance_csv,
    create_gate,
    get_event_stats,
    get_gate_status,
    get_my_registrations,
    get_my_scan_stats,
    get_my_ticket_stats,
    get_or_create_google_user,
    issue_ticket,
    leave_waitlist,
    record_scan,
    revoke_ticket,
)
from .google_auth import verify_google_id_token


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    create_database()
    with SessionLocal() as db:
        seed_reference_data(db)
        # Opt-in only. Creates demo/demo123, so it is for previews and demos.
        if demo_seed_enabled():
            result = seed_demo_data(db)
            if result.get("seeded"):
                logger.info("Seeded the demo dataset. Sign in as %s", result.get("credentials"))
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


@app.post("/auth/google", response_model=LoginResponse)
def google_login(payload: GoogleLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    if payload.role == "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin registration is not allowed")
    google_info = verify_google_id_token(payload.id_token)
    user = get_or_create_google_user(db, google_info, payload.role)
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

    # Delete waitlist entries for this event
    db.query(WaitlistEntry).filter(WaitlistEntry.event_id == event_id).delete(synchronize_session=False)

    # Delete gates for this event
    db.query(Gate).filter(Gate.event_id == event_id).delete(synchronize_session=False)

    db.delete(event)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/events/{event_id}/stats", response_model=EventStatsOut)
def event_stats(
    event_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> EventStatsOut:
    return get_event_stats(db, event_id)


@app.get("/events/{event_id}/export")
def export_attendance_csv(
    event_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    csv_data = build_attendance_csv(db, event_id)
    return StreamingResponse(
        io.StringIO(csv_data),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="event-{event_id}-attendance.csv"'
        },
    )


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


@app.post("/tickets", response_model=TicketIssuanceResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    response: Response,
    current_user: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> TicketIssuanceResponse:
    result = issue_ticket(db, payload, user_id=current_user.id)
    if isinstance(result, WaitlistEntry):
        return WaitlistJoinResponse(
            outcome="waitlisted",
            waitlisted=True,
            position=result.position,
            event_id=result.event_id,
            waitlist_entry_id=result.id,
        )
    response.headers["Location"] = f"/tickets/{result.id}"
    return TicketCreated(
        outcome="ticketed",
        ticket_id=result.id,
        qr_signature=result.qr_signature or "",
        tier=result.tier,
        seat_number=result.seat_number or "",
        status=result.status,
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


@app.post("/tickets/{ticket_id}/revoke", response_model=RevokeTicketResponse)
def revoke_ticket_endpoint(
    ticket_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> RevokeTicketResponse:
    ticket = revoke_ticket(db, ticket_id)
    return RevokeTicketResponse(
        ticket_id=ticket.id,
        status=ticket.status,
        revoked_at=ticket.revoked_at,
        promoted_attendee=getattr(ticket, "promoted_attendee", None),
    )


@app.get("/events/{event_id}/waitlist", response_model=list[WaitlistEntryOut])
def list_event_waitlist(
    event_id: int,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> list[WaitlistEntry]:
    return (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.event_id == event_id, WaitlistEntry.status == "waiting")
        .order_by(WaitlistEntry.position.asc())
        .all()
    )


@app.get("/me/registrations", response_model=MyRegistrations)
def my_registrations(
    current_user: User = Depends(require_roles("user", "scanner", "admin")),
    db: Session = Depends(get_db),
) -> MyRegistrations:
    """The caller's own waitlist places.

    ET-07's in-app notice polls this: it is how a waitlisted attendee recovers
    their position after a reload, and how they collect the ticket ET-11's
    revocation issued them.
    """
    return get_my_registrations(db, current_user)


@app.delete("/me/waitlist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_from_waitlist(
    entry_id: int,
    current_user: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> Response:
    entry = db.get(WaitlistEntry, entry_id)
    if entry is None or entry.user_id != current_user.id:
        # Do not distinguish "not yours" from "not there".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waitlist entry not found")
    if entry.status != "waiting":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Waitlist entry is no longer active")

    leave_waitlist(db, entry)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _suggestion_signature(ranking, events: list[Event]) -> str:
    """Identify the state the copy was written for.

    Seat counts and the caller's own registrations both change what a reason can
    legitimately say, so both go into the digest. Without that, a cached sentence
    could keep describing "3 seats left" after the last one is taken.
    """
    parts = [f"{event.id}:{event.issued_count}:{event.capacity}" for event in events]
    parts.append("own:" + ",".join(str(event_id) for event_id in sorted(ranking.registered_event_ids)))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


@app.get("/me/event-suggestions", response_model=EventSuggestionOut)
def event_suggestions(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> EventSuggestionOut:
    """Events to recommend to the caller, ranked without a model.

    The ranking is deterministic and computed on every call, so it is never stale.
    Only the one-line reason can come from Groq, and only from a cache: a request
    never waits on the model. On a cold cache the rule-based reason is returned
    immediately and the model call is queued as a background refresh for next
    time.

    Unlike the Google login integration, a missing API key is not an error. This
    row is supplementary, so it degrades to rule-based copy rather than 503.
    """
    ranking = rank_events(db, current_user)
    ai_enabled = groq.is_configured()

    if not ranking.suggestions:
        return EventSuggestionOut(items=[], cold_start=ranking.cold_start, ai_enabled=ai_enabled)

    ordered_events = [suggestion.event for suggestion in ranking.suggestions]
    signature = _suggestion_signature(ranking, ordered_events)
    cached = groq.read_cache(current_user.id, signature)

    if cached is None and ai_enabled:
        # Materialised here, while the session is still open: the refresh runs
        # after the response is sent and must not touch these ORM rows.
        prompt_items = [
            groq.PromptSuggestion(
                event=groq.PromptEvent(
                    id=event.id,
                    title=event.title,
                    venue=event.venue,
                    date_time=event.date_time,
                    capacity=event.capacity,
                    seats_left=max(event.capacity - event.issued_count, 0),
                    tiers=", ".join(
                        f"{item['tier']}:{item['issued_count']}" for item in event.tier_counts
                    ) or "none issued",
                    registered=event.issued_count,
                ),
                signal=suggestion.signal,
            )
            for event, suggestion in zip(ordered_events, ranking.suggestions)
        ]
        background_tasks.add_task(
            _refresh_suggestion_copy,
            current_user.id,
            signature,
            prompt_items,
            ranking.cold_start,
        )

    items = []
    for suggestion in ranking.suggestions:
        from_ai = cached.get(suggestion.event.id) if cached else None
        items.append(
            EventSuggestion(
                event=EventOut.model_validate(suggestion.event),
                reason=from_ai or suggestion.reason,
                source="ai" if from_ai else "rule",
                signal=signal_label(suggestion.signal),
                score=round(suggestion.score, 4),
            )
        )

    return EventSuggestionOut(
        items=items,
        cold_start=ranking.cold_start,
        ai_enabled=ai_enabled,
    )


def _refresh_suggestion_copy(
    user_id: int,
    signature: str,
    prompt_items: list[groq.PromptSuggestion],
    cold_start: bool,
) -> None:
    """Populate the cache for next time. Failures here are silent by design."""
    groq.write_reasons(
        user_id=user_id,
        signature=signature,
        suggestions=prompt_items,
        cold_start=cold_start,
    )



@app.post("/scans", response_model=ScanResult)
def scan_ticket(
    payload: ScanCreate,
    current_user: User = Depends(require_roles("scanner")),
    db: Session = Depends(get_db),
) -> ScanResult:
    return record_scan(db, payload, user_id=current_user.id)


@app.get("/me/scans", response_model=MyScanStats)
def my_scans(
    current_user: User = Depends(require_roles("scanner")),
    db: Session = Depends(get_db),
) -> MyScanStats:
    return get_my_scan_stats(db, current_user)


@app.get("/me/ticket-stats", response_model=MyTicketStats)
def my_ticket_stats(
    current_user: User = Depends(require_roles("user")),
    db: Session = Depends(get_db),
) -> MyTicketStats:
    return get_my_ticket_stats(db, current_user)


@app.get("/gates/status", response_model=list[GateStatus])
def gates_status(
    event_id: int | None = Query(default=None),
    _: User = Depends(require_roles("admin", "scanner")),
    db: Session = Depends(get_db),
) -> list[GateStatus]:
    return get_gate_status(db, event_id=event_id)
