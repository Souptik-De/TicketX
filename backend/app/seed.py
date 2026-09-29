from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Attendee, Event, Gate, Scan, Ticket, User, Volunteer, WaitlistEntry
from .schemas import ScanCreate, TicketCreate
from .services import issue_ticket, record_scan


def seed_reference_data(db: Session) -> None:
    if db.query(User).count() == 0:
        db.add_all(
            [
                User(username="attendee", password_hash=hash_password("attendee123"), role="user", display_name="Student User"),
                User(username="admin", password_hash=hash_password("admin123"), role="admin", display_name="Event Admin"),
                User(username="scanner", password_hash=hash_password("scanner123"), role="scanner", display_name="Gate Scanner"),
            ]
        )

    if db.query(Event).count() == 0:
        db.add(
            Event(
                title="Techno Cultural Fest 2026",
                description="A campus-wide evening of live music, technology showcases, performances, and student exhibitions.",
                date_time=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=14),
                venue="Main Auditorium",
                capacity=500,
            )
        )
        db.flush()

    event = db.query(Event).order_by(Event.id.asc()).first()

    if event and not event.description:
        event.description = "A campus-wide evening of live music, technology showcases, performances, and student exhibitions."

    # The seeded event is created at now + 14 days, so on any database that has been
    # sitting for a fortnight it has quietly become a past event: it disappears
    # from recommendations and the catalog shows a dead listing. Rolling it forward
    # keeps a local database usable. A real deployment has admin-created events, so
    # this only ever touches the seeded row.
    if event and event.date_time <= datetime.now(UTC).replace(tzinfo=None):
        event.date_time = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=14)

    if db.query(Gate).count() == 0:
        main_gate = Gate(name="Main Gate", location="Auditorium front entrance", event_id=event.id if event else None)
        side_gate = Gate(name="Side Gate", location="Canteen walkway", event_id=event.id if event else None)
        db.add_all([main_gate, side_gate])
        db.flush()
        db.add_all(
            [
                Volunteer(name="Aparna Dutta", gate_id=main_gate.id),
                Volunteer(name="Soumyadip Das", gate_id=main_gate.id),
                Volunteer(name="Souptik De", gate_id=side_gate.id),
            ]
        )
    else:
        db.query(Gate).filter(Gate.event_id.is_(None)).update({"event_id": event.id if event else None})

    db.commit()


# ---------------------------------------------------------------------------
# Optional demo dataset
# ---------------------------------------------------------------------------
#
# Off unless TICKETX_SEED_DEMO is set to 1/true/yes/on. See config.demo_seed_enabled.
#
# It exists so the recommendation feature can be demonstrated without waiting for
# real usage, which matters because TicketX's own database is almost empty: the
# scorer has nothing to rank from, so a demo has to be assembled deliberately.
#
# The shape is designed so each ranking signal is visible for the demo account
# rather than only in aggregate -- see DEMO_PLAN below. Tickets are issued through
# the real issue_ticket service, so seat numbers and QR signatures are genuine and
# the "My tickets" list and ticket pass render properly.
#
# WARNING: this creates an account with a fixed, publicly documented password. Use
# it for internal previews and demos only. Turn the flag off for a real deployment.

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo123"
DEMO_DISPLAY_NAME = "Riya Demo"
DEMO_CREDENTIALS = f"{DEMO_USERNAME} / {DEMO_PASSWORD}"

#: Marker for "the demo is already here". Checked so a restart with the flag on
#: cannot duplicate rows.
DEMO_MARKER_TITLE = "Robotics Workshop"

#: Every demo attendee carries this prefix, which is what makes cleanup possible
#: without guessing: it can delete demo rows while leaving real ones alone.
DEMO_CAMPUS_PREFIX = "DEMO-"


def demo_attendee_campus(tag: str) -> str:
    return f"{DEMO_CAMPUS_PREFIX}{tag}"


# (title, description, venue, capacity, days_from_now)
DEMO_EVENTS = [
    (
        "Robotics Workshop",
        "Soldering, microcontrollers, and autonomous navigation with the robotics club.",
        "Innovation Hall",
        40,
        12,
    ),
    (
        "Hackathon Weekend",
        "Build an overnight prototype with mentors on hand, then demo it on Sunday.",
        "Innovation Hall",
        60,
        19,
    ),
    (
        "Startup Bootcamp Demo Day",
        "Capstone teams pitch their product to employers and investors.",
        "Main Auditorium",
        80,
        26,
    ),
    (
        "Poetry Slam",
        "Spoken word verses followed by a signed open mic.",
        "Literature Centre",
        50,
        33,
    ),
    (
        "Astronomy Night",
        "Telescopes on the roof deck; Saturn is visible from dusk.",
        "Observatory Deck",
        45,
        40,
    ),
    (
        "Neon Nights Showcase",
        "Student bands and DJs in the main hall, late seating only.",
        "Main Auditorium",
        2,
        47,
    ),
]

# A past event, so the "upcoming only" rule is visible rather than assumed.
DEMO_PAST_EVENT = ("Last Year's Fest", "Already happened. Kept so past events can be seen being excluded.", "Hall C")


# ---------------------------------------------------------------------------
# Stats showcase: one event with tickets across all tiers and check-ins
# spread across its gates, so the admin event-stats page (metrics, tier and
# gate tables, and the charts) has something real to show.
# ---------------------------------------------------------------------------
#
# Seeded together with the demo dataset, behind the same TICKETX_SEED_DEMO
# flag, and cleaned up by the same clear_demo_data (titles + DEMO- prefix),
# so previews get the charts for free and production stays untouched.

SHOWCASE_TITLE = "Freshers Showcase Night"
SHOWCASE_DESCRIPTION = "A full house to click through: tiered tickets and gate check-ins for the event stats charts."
SHOWCASE_VENUE = "Seminar Hall"
SHOWCASE_CAPACITY = 250
SHOWCASE_DAYS_FROM_NOW = 18

SHOWCASE_GATES = [
    ("Main Gate", "Auditorium front entrance", "Showcase Volunteer Main"),
    ("VIP Gate", "Auditorium east entrance", "Showcase Volunteer VIP"),
    ("Side Gate", "Canteen walkway", "Showcase Volunteer Side"),
]

# (attendee name, campus tag suffix, tier)
SHOWCASE_TICKETS = [
    ("Aarav Sharma", "SHOW01", "general"),
    ("Diya Patel", "SHOW02", "general"),
    ("Kabir Singh", "SHOW03", "general"),
    ("Ananya Iyer", "SHOW04", "general"),
    ("Vikram Rao", "SHOW05", "general"),
    ("Sneha Kulkarni", "SHOW06", "general"),
    ("Arjun Nair", "SHOW07", "general"),
    ("Pooja Verma", "SHOW08", "general"),
    ("Riya Sen", "SHOW09", "premium"),
    ("Aditya Mehta", "SHOW10", "premium"),
    ("Ishita Bose", "SHOW11", "premium"),
    ("Rohan Gupta", "SHOW12", "premium"),
    ("Neha Joshi", "SHOW13", "premium"),
    ("Souptik De", "SHOW14", "vip"),
    ("Aparna Dutta", "SHOW15", "vip"),
    ("Soumyadip Das", "SHOW16", "vip"),
]

# (ticket index into SHOWCASE_TICKETS, gate index into SHOWCASE_GATES).
# Eleven of sixteen checked in, so the donut, tier bars, and gate bars move
# while five tickets stay outside.
SHOWCASE_SCANS = [
    (0, 0),
    (1, 0),
    (2, 0),
    (4, 0),
    (8, 0),
    (9, 1),
    (10, 1),
    (13, 0),
    (14, 1),
    (6, 2),
    (7, 2),
]


def demo_data_exists(db: Session) -> bool:
    return db.query(Event).filter(Event.title == DEMO_MARKER_TITLE).first() is not None


def clear_demo_data(db: Session) -> None:
    """Remove only what seed_demo_data created.

    Scoped by the demo event titles and the DEMO- campus prefix, so real events,
    real attendees, and real accounts are never touched even though they live in
    the same tables.
    """
    titles = [row[0] for row in DEMO_EVENTS] + [DEMO_PAST_EVENT[0], SHOWCASE_TITLE]
    events = db.query(Event).filter(Event.title.in_(titles)).all()
    event_ids = [event.id for event in events]

    # Demo attendees can also hold tickets outside the demo events (the demo
    # account books the reference fest), so scope ticket cleanup by attendee
    # too. Otherwise a rebuild reuses row ids and trips the duplicate guard.
    demo_attendee_ids = select(Attendee.id).where(Attendee.campus_id.like(f"{DEMO_CAMPUS_PREFIX}%"))
    demo_ticket_ids = select(Ticket.id).where(
        Ticket.event_id.in_(event_ids) | Ticket.attendee_id.in_(demo_attendee_ids)
    )
    showcase_gate_ids = select(Gate.id).where(Gate.event_id.in_(event_ids))

    db.query(Scan).filter(Scan.ticket_id.in_(demo_ticket_ids)).delete(synchronize_session=False)
    db.query(WaitlistEntry).filter(WaitlistEntry.event_id.in_(event_ids)).delete(synchronize_session=False)
    db.query(Ticket).filter(Ticket.id.in_(demo_ticket_ids)).delete(synchronize_session=False)
    db.query(Volunteer).filter(Volunteer.gate_id.in_(showcase_gate_ids)).delete(synchronize_session=False)
    db.query(Gate).filter(Gate.id.in_(showcase_gate_ids)).delete(synchronize_session=False)
    db.query(Attendee).filter(Attendee.campus_id.like(f"{DEMO_CAMPUS_PREFIX}%")).delete(synchronize_session=False)

    for event in events:
        db.delete(event)

    db.query(User).filter(User.username == DEMO_USERNAME).delete(synchronize_session=False)
    db.commit()


def _book(db: Session, event: Event, name: str, campus: str, *, tier: str = "general", user_id: int | None = None) -> None:
    """Issue one ticket through the real service.

    Passing user_id=None produces the legacy shape: a real attendee row with no
    owning account. That is what production's pre-migration rows look like, and it
    is why the crowd index is keyed on attendee_id.
    """
    issue_ticket(
        db,
        TicketCreate(
            event_id=event.id,
            attendee_name=name,
            attendee_contact=f"{campus.lower()}@example.edu",
            campus_id=campus,
            tier=tier,
        ),
        user_id=user_id,
    )


def seed_stats_showcase(db: Session, now) -> dict:
    """Seed the stats/charts showcase event. Idempotent without ``force``."""
    if db.query(Event).filter(Event.title == SHOWCASE_TITLE).first() is not None:
        return {"seeded": False, "reason": "already present"}

    event = Event(
        title=SHOWCASE_TITLE,
        description=SHOWCASE_DESCRIPTION,
        date_time=now + timedelta(days=SHOWCASE_DAYS_FROM_NOW),
        venue=SHOWCASE_VENUE,
        capacity=SHOWCASE_CAPACITY,
    )
    db.add(event)
    db.flush()

    gates: list[Gate] = []
    volunteers: list[Volunteer] = []
    for gate_name, location, volunteer_name in SHOWCASE_GATES:
        gate = Gate(name=gate_name, location=location, event_id=event.id)
        db.add(gate)
        db.flush()
        volunteer = Volunteer(name=volunteer_name, gate_id=gate.id)
        db.add(volunteer)
        gates.append(gate)
        volunteers.append(volunteer)
    db.flush()

    tickets: list[Ticket] = []
    for name, tag, tier in SHOWCASE_TICKETS:
        campus = demo_attendee_campus(tag)
        issued = issue_ticket(
            db,
            TicketCreate(
                event_id=event.id,
                attendee_name=name,
                attendee_contact=f"{campus.lower()}@example.edu",
                campus_id=campus,
                tier=tier,
            ),
        )
        if isinstance(issued, Ticket):
            tickets.append(issued)
    db.flush()

    checked = 0
    for ticket_index, gate_index in SHOWCASE_SCANS:
        if ticket_index < len(tickets) and gate_index < len(gates):
            record_scan(
                db,
                ScanCreate(
                    ticket_id=tickets[ticket_index].id,
                    gate_id=gates[gate_index].id,
                    volunteer_id=volunteers[gate_index].id,
                ),
            )
            checked += 1
    db.commit()

    return {"seeded": True, "event": event.title, "issued": len(tickets), "checked_in": checked}


def seed_demo_data(db: Session, *, force: bool = False) -> dict:
    """Populate the demo dataset. Idempotent unless ``force`` is set."""
    if demo_data_exists(db):
        if not force:
            return {"seeded": False, "reason": "already present"}
        clear_demo_data(db)

    now = datetime.now(UTC).replace(tzinfo=None)

    events: dict[str, Event] = {}
    for title, description, venue, capacity, days in DEMO_EVENTS:
        event = Event(
            title=title,
            description=description,
            date_time=now + timedelta(days=days),
            venue=venue,
            capacity=capacity,
        )
        db.add(event)
        events[title] = event

    past = Event(
        title=DEMO_PAST_EVENT[0],
        description=DEMO_PAST_EVENT[1],
        date_time=now - timedelta(days=30),
        venue=DEMO_PAST_EVENT[2],
        capacity=100,
    )
    db.add(past)

    demo_user = db.query(User).filter(User.username == DEMO_USERNAME).one_or_none()
    if demo_user is None:
        demo_user = User(
            username=DEMO_USERNAME,
            password_hash=hash_password(DEMO_PASSWORD),
            role="user",
            display_name=DEMO_DISPLAY_NAME,
        )
        db.add(demo_user)
        db.flush()

    db.commit()

    # The demo account's own history. Two venues, both of which have other future
    # events, so venue affinity has something to point at later.
    _book(db, events["Robotics Workshop"], DEMO_DISPLAY_NAME, demo_attendee_campus("RIYA"), tier="premium", user_id=demo_user.id)
    fest = db.query(Event).filter(Event.title == "Techno Cultural Fest 2026").one_or_none()
    if fest is not None:
        _book(db, fest, DEMO_DISPLAY_NAME, demo_attendee_campus("RIYA2"), user_id=demo_user.id)

    # Two peers who booked Robotics Workshop and then also booked Poetry Slam.
    # That overlap is the only thing that can surface Poetry Slam for this account.
    for tag in ("PEER1", "PEER2"):
        campus = demo_attendee_campus(tag)
        _book(db, events["Robotics Workshop"], f"Peer {tag}", campus)
        _book(db, events["Poetry Slam"], f"Peer {tag}", campus)

    # A premium seat at Hackathon Weekend, so the demo account's tier preference
    # has a match, plus a little general interest.
    _book(db, events["Hackathon Weekend"], "Premium Fan", demo_attendee_campus("PREMFAN"), tier="premium")
    for tag in ("HACK1", "HACK2"):
        _book(db, events["Hackathon Weekend"], f"Hacker {tag}", demo_attendee_campus(tag))

    # An event nobody in the demo account's circle has touched, so it can only be
    # recommended on popularity.
    for index in range(1, 6):
        _book(db, events["Astronomy Night"], f"Stargazer {index}", demo_attendee_campus(f"STAR{index}"))

    # Startup Demo Day shares Main Auditorium with the demo account's second
    # ticket, so it is reachable by venue too.
    for tag in ("START1", "START2"):
        _book(db, events["Startup Bootcamp Demo Day"], f"Founders {tag}", demo_attendee_campus(tag))

    # Fill Neon Nights Showcase, then ask for one more so the waitlist branch of
    # issue_ticket runs for real rather than being faked.
    for tag in ("NEON1", "NEON2"):
        _book(db, events["Neon Nights Showcase"], f"Neon {tag}", demo_attendee_campus(tag))
    _book(db, events["Neon Nights Showcase"], "Too Late", demo_attendee_campus("NEONWAIT"))

    showcase = seed_stats_showcase(db, now)

    return {
        "seeded": True,
        "credentials": DEMO_CREDENTIALS,
        "events": len(DEMO_EVENTS) + 2,
        "showcase": showcase,
        "reason": None,
    }
