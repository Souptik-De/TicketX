"""Event suggestion tests.

Nothing here touches the network. The Groq client is monkeypatched, and the test
that inspects the prompt asserts against the payload the real code builds.

The scorer is exercised directly rather than only through the endpoint, because
its whole job is to stay correct on a database that is mostly empty -- which is
the state TicketX actually runs in.
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_PATH = Path(tempfile.gettempdir()) / f"ticketx-suggest-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"

from backend.app import groq  # noqa: E402
from backend.app.database import Base, SessionLocal, engine  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Attendee, Event, Ticket, User  # noqa: E402
from backend.app.recommend import _cooccurrence, _confidence, rank_events  # noqa: E402
from backend.app.seed import seed_reference_data  # noqa: E402


def teardown_module() -> None:
    groq.clear_cache()
    engine.dispose()
    TEST_DATABASE_PATH.unlink(missing_ok=True)


def reset_database() -> None:
    groq.clear_cache()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_reference_data(db)
    finally:
        db.close()


def auth_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


# Distinct titles and descriptions, so a test can switch off the venue and topic
# signals and let one signal decide the ranking.
_ISOLATED_TOPICS = [
    ("Robotics Workshop", "Soldering, microcontrollers, and autonomous navigation."),
    ("Poetry Slam", "Spoken word verses followed by an open mic."),
    ("Debate Championship", "Parliamentary style rounds on campus policy."),
    ("Street Dance-Off", "Crew choreography battles judged by three panels."),
    ("Astronomy Night", "Telescopes on the roof deck, Saturn visible from dusk."),
]


def add_events(
    db,
    count: int,
    *,
    venue: str = "Main Auditorium",
    isolate: bool = False,
    distinct_topics: bool = False,
    start_day: int = 1,
) -> list[int]:
    """Upcoming events on consecutive days, so ordering is predictable.

    Returns ids rather than ORM objects: a committed instance is expired, so
    touching an attribute after the session closes raises DetachedInstanceError.

    The flags exist so a test can switch individual signals off. Left alone, every
    candidate shares a venue, a title stem, and a description with the others, so
    venue and topic affinity both fire for all of them and no single signal is ever
    what decides the ranking.

    ``isolate`` switches off venue and topic together. ``distinct_topics`` keeps the
    shared venue, which is what a test about venue affinity needs.
    """
    unshared_topics = isolate or distinct_topics
    created = [
        Event(
            title=(_ISOLATED_TOPICS[index % len(_ISOLATED_TOPICS)][0] if unshared_topics else f"Future Fest {index}"),
            description=(
                _ISOLATED_TOPICS[index % len(_ISOLATED_TOPICS)][1] if unshared_topics
                else f"Live music and showcases for evening {index}."
            ),
            date_time=datetime(2027, 6, start_day + index, 18, 0),
            venue=f"{venue} {index}" if isolate else venue,
            capacity=100,
        )
        for index in range(count)
    ]
    db.add_all(created)
    db.commit()
    return [event.id for event in created]


def _attendee(db, campus_id: str, name: str = "Someone") -> Attendee:
    attendee = db.query(Attendee).filter(Attendee.campus_id == campus_id).one_or_none()
    if attendee is None:
        attendee = Attendee(name=name, campus_id=campus_id, contact_email=f"{campus_id}@example.edu")
        db.add(attendee)
        db.flush()
    return attendee


def owned_ticket(db, user: User, event_id: int, *, tier: str = "general", campus_id: str) -> Ticket:
    """A ticket owned by a specific account, which is what own-history reads."""
    ticket = Ticket(
        event_id=event_id,
        attendee_id=_attendee(db, campus_id).id,
        tier=tier,
        seat_number=f"SEAT-{campus_id}",
        status="issued",
        user_id=user.id,
    )
    db.add(ticket)
    db.commit()
    return ticket


def crowd_ticket(db, event_id: int, *, tier: str = "general", campus_id: str) -> Ticket:
    """A ticket with no owning account, the shape pre-migration rows have."""
    ticket = Ticket(
        event_id=event_id,
        attendee_id=_attendee(db, campus_id).id,
        tier=tier,
        seat_number=f"SEAT-{campus_id}",
        status="issued",
        user_id=None,
    )
    db.add(ticket)
    db.commit()
    return ticket


def prompt_item(event_id: int = 1, *, registered: int = 0) -> groq.PromptSuggestion:
    return groq.PromptSuggestion(
        event=groq.PromptEvent(
            id=event_id,
            title="A Fest",
            venue="Hall",
            date_time=datetime(2027, 6, 1, 18, 0),
            capacity=10,
            seats_left=4,
            tiers="general:6",
            registered=registered,
        ),
        signal="crowd_popularity",
    )


def clear_events(db) -> None:
    """Remove the seeded event so a test can count suggestions exactly."""
    db.query(Event).delete()
    db.commit()


def fake_completion(reason_for: str = "Worth a look.", *, status_code: int = 200):
    """A stand-in for httpx.post that echoes back the event ids it was offered.

    Patched at the network boundary rather than at ``write_reasons`` on purpose:
    the cache is written by that function, so replacing it would test nothing
    about caching.
    """
    import json as json_module
    import re as re_module

    class Response:
        def __init__(self, status: int, payload: dict, text: str = ""):
            self.status_code = status
            self._payload = payload
            self.text = text

        def json(self) -> dict:
            return self._payload

    def post(_url, *, headers, json, timeout):
        assert headers["Authorization"].startswith("Bearer ")
        user_message = json["messages"][-1]["content"]
        offered = [int(value) for value in re_module.findall(r"event_id (\d+):", user_message)]
        content = json_module.dumps(
            {"reasons": [{"event_id": event_id, "reason": reason_for} for event_id in offered]}
        )
        body = {"choices": [{"message": {"content": content}}]}
        return Response(status_code, body, text=content)

    return post


# --- the scorer -----------------------------------------------------------


def test_cold_start_still_returns_real_events() -> None:
    """An attendee with no history must still get registrable suggestions.

    TicketX runs on a database where most accounts own nothing, so an empty row
    would be the common case rather than an edge case.
    """
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        add_events(db, 3)
        ranking = rank_events(db, user)
    finally:
        db.close()

    assert ranking.cold_start is True
    assert len(ranking.suggestions) == 3
    assert all(item.event.date_time > datetime.now() for item in ranking.suggestions)
    # Cold-start copy must not imply a personal connection the data cannot support.
    assert all("you" not in item.reason.lower() for item in ranking.suggestions)


def test_own_history_outranks_a_stranger_event() -> None:
    """A venue the caller has used should beat a busier event they have not.

    The stranger's event is the most popular thing in the database, so the
    crowd signal for it is at its maximum. The caller's own venue still has to
    come out ahead.
    """
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        clear_events(db)
        # Two past bookings at the same venue, so the venue is genuinely familiar
        # and the confidence factor reflects more than one observation.
        for index, when in enumerate((datetime(2026, 1, 5), datetime(2026, 2, 5))):
            db.add(Event(
                title=f"Winter Sessions {index}",
                description="An earlier event at this venue.",
                date_time=when,
                venue="Cellar Stage",
                capacity=100,
            ))
        db.commit()
        past_ids = [row.id for row in db.query(Event).order_by(Event.date_time).all()]
        for index, past_id in enumerate(past_ids):
            owned_ticket(db, user, past_id, campus_id=f"CAMP-PAST{index}")

        familiar = Event(
            title="Late Set at the Cellar",
            description="Unrelated subject matter entirely.",
            date_time=datetime(2027, 4, 1, 18, 0),
            venue="Cellar Stage",
            capacity=100,
        )
        stranger = Event(
            title="Observatory Fundraiser",
            description="Also unrelated, and much busier.",
            date_time=datetime(2027, 4, 2, 18, 0),
            venue="Observatory Deck",
            capacity=100,
        )
        db.add_all([familiar, stranger])
        db.commit()
        familiar_id, stranger_id = familiar.id, stranger.id

        for index in range(5):
            crowd_ticket(db, stranger_id, campus_id=f"CAMP-CROWD{index}")
        db.commit()

        ranking = rank_events(db, user)
    finally:
        db.close()

    top = ranking.suggestions[0]
    assert top.event.id == familiar_id
    assert top.signal == "own_venue"
    assert "where you have been before" in top.reason
    # The busier event is still offered, just not first.
    order = [item.event.id for item in ranking.suggestions]
    assert order.index(familiar_id) < order.index(stranger_id)
    assert ranking.cold_start is False


def test_the_default_tier_is_not_treated_as_a_preference() -> None:
    """Choosing "general" is the absence of a choice, not a preference.

    Every event has general seating, and almost every attendee takes it, so scoring
    a general-only history as a full-strength preference made the weakest signal
    outrank the ones that actually distinguish an attendee.
    """
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        events = add_events(db, 3, isolate=True)
        # The caller only ever books general, and so does the crowd.
        owned_ticket(db, user, events[0], tier="general", campus_id="CAMP-PLAIN")
        crowd_ticket(db, events[1], campus_id="CAMP-CROWD")
        ranking = rank_events(db, user)
    finally:
        db.close()

    # Nothing in this history is distinctive, so no tier claim is made.
    assert all(item.signal != "own_tier" for item in ranking.suggestions)
    assert all("usually pick" not in item.reason for item in ranking.suggestions)


def test_a_premium_preference_is_recognised_against_a_general_crowd() -> None:
    """The flip side: a real over-preference is still detected."""
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        events = add_events(db, 2, isolate=True)
        owned_ticket(db, user, events[0], tier="premium", campus_id="CAMP-VIP")
        # The crowd is overwhelmingly general.
        for index in range(1, 5):
            crowd_ticket(db, events[0], campus_id=f"CAMP-GENERAL{index}", tier="general")
        crowd_ticket(db, events[1], campus_id="CAMP-GENERAL5", tier="general")
        # Give the candidate premium seating, or the match cannot be made.
        crowd_ticket(db, events[1], campus_id="CAMP-VIPFAN", tier="premium")
        db.commit()
        ranking = rank_events(db, user)
    finally:
        db.close()

    top = ranking.suggestions[0]
    assert top.event.id == events[1]
    assert top.signal == "own_tier"
    assert "premium" in top.reason


def test_already_registered_and_past_events_are_never_suggested() -> None:
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        events = add_events(db, 2)
        owned_ticket(db, user, events[0], campus_id="CAMP-HELD")
        past = Event(
            title="Last Year Fest",
            description="Already happened.",
            date_time=datetime.now() - timedelta(days=30),
            venue="Main Auditorium",
            capacity=100,
        )
        db.add(past)
        db.commit()
        ranking = rank_events(db, user)
    finally:
        db.close()

    offered = {item.event.id for item in ranking.suggestions}
    assert events[0] not in offered
    assert past.id not in offered
    assert events[1] in offered


def test_cooccurrence_breaks_a_popularity_tie() -> None:
    """Co-occurrence decides between two equally popular candidates.

    Co-occurrence is bounded by popularity, since a peer who shared the caller's
    event also holds a ticket for the candidate. So its real job is breaking ties
    and reordering, not out-scoring popularity outright -- which is what this
    asserts. ``test_cooccurrence_component_is_computed`` covers the value itself.
    """
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        events = add_events(db, 3, isolate=True)
        # The caller only ever books VIP, so tier affinity cannot decide this.
        owned_ticket(db, user, events[0], tier="vip", campus_id="CAMP-CALLER")

        # One ticket each, so both candidates are equally popular, but the peer who
        # shares event 0 also holds event 2 and not event 1.
        peer = _attendee(db, "CAMP-PEER", name="Peer")
        db.add_all(
            [
                Ticket(event_id=events[0], attendee_id=peer.id, tier="general",
                       seat_number="SEAT-PEER-1", status="issued"),
                Ticket(event_id=events[2], attendee_id=peer.id, tier="general",
                       seat_number="SEAT-PEER-2", status="issued"),
            ]
        )
        db.commit()
        ranking = rank_events(db, user)
    finally:
        db.close()

    # Event 2 is no more popular than event 1, but it is the one the peer shares,
    # so it must outrank it. Compared by position rather than by index, because
    # the seeded event is also a candidate and sorts in between them.
    order = [item.event.id for item in ranking.suggestions]
    assert order.index(events[2]) < order.index(events[1])


def test_cooccurrence_component_is_computed() -> None:
    """The co-occurrence value, isolated from the rest of the score.

    Asserted as a conditional share: the reading must not change when the crowd
    grows, which is what a raw count against the total crowd did.
    """
    by_attendee = {
        1: {10, 20},  # shared the caller's event and the candidate
        2: {10, 20},  # likewise
        3: {10},      # shared the caller's event but not the candidate
        4: {99},      # unrelated attendee, must not dilute anything
    }

    result = _cooccurrence([10], 20, by_attendee)
    assert result.peers == 2
    assert result.anchor_event_id == 10
    # Two of the three peers who shared event 10 also booked 20. The unrelated
    # attendee is not in that pool, and the value is damped by the follower count.
    assert result.share == pytest.approx((2 / 3) * (2 / 3))

    # Adding more unrelated attendees must not change the reading.
    grown = dict(by_attendee)
    grown.update({index: {50 + index} for index in range(5, 60)})
    assert _cooccurrence([10], 20, grown).share == pytest.approx(result.share)

    # A candidate nobody followed them to scores nothing.
    assert _cooccurrence([10], 99, by_attendee).share == 0.0
    # No history, or no peers at all, or an empty crowd: no crash, no contribution.
    assert _cooccurrence([], 20, by_attendee).share == 0.0
    assert _cooccurrence([10], 20, {}).share == 0.0


def test_the_caller_is_not_counted_as_their_own_peer() -> None:
    """The caller shares their own events by definition.

    Leaving them in the peer pool inflated the denominator and roughly halved the
    co-occurrence signal on every account.
    """
    by_attendee = {
        7: {10},        # the caller
        1: {10, 20},    # a real follower
        2: {10, 20},    # another real follower
    }

    without_exclusion = _cooccurrence([10], 20, by_attendee)
    assert without_exclusion.peers == 2
    assert without_exclusion.share == pytest.approx((2 / 3) * (2 / 3))

    with_exclusion = _cooccurrence([10], 20, by_attendee, exclude_attendee_ids={7})
    assert with_exclusion.peers == 2
    assert with_exclusion.share == pytest.approx(1.0 * (2 / 3))


def test_a_single_observation_never_claims_a_firm_preference() -> None:
    """One booking is not "usually".

    Personal signals are damped by how much history supports them, so a lone
    registration cannot produce a confident-sounding reason.
    """
    assert _confidence(0) == 0.0
    assert _confidence(1) == pytest.approx(0.5)
    assert _confidence(4) == pytest.approx(0.8)
    # Rises with evidence and never reaches certainty.
    values = [_confidence(n) for n in range(1, 40)]
    assert values == sorted(values)
    assert all(value < 1 for value in values)


def test_cooccurrence_names_the_event_the_peers_actually_attended() -> None:
    """The reason credits the event the followers booked, not the caller's first.

    It previously named an event the followers had never registered for, which is
    simply a false statement about real people.
    """
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        # Clear the seeded event: it is future-dated and would take the first slot
        # on a score tie, hiding the signal this test is about.
        clear_events(db)
        # The caller books two events; the follower only ever follows to the second.
        owned = add_events(db, 4, isolate=True)
        owned_ticket(db, user, owned[0], campus_id="CAMP-CALLER-A")
        owned_ticket(db, user, owned[2], campus_id="CAMP-CALLER-B")
        titles = {
            row.id: row.title
            for row in db.query(Event).filter(Event.id.in_(owned)).all()
        }

        peer = _attendee(db, "CAMP-FOLLOWER", name="Follower")
        # The follower booked the caller's second event and then went on to the
        # candidate. The caller's *first* event is not among the follower's.
        db.add_all(
            [
                Ticket(event_id=owned[2], attendee_id=peer.id, tier="general",
                       seat_number="SEAT-FOLLOWER-1", status="issued"),
                Ticket(event_id=owned[3], attendee_id=peer.id, tier="general",
                       seat_number="SEAT-FOLLOWER-2", status="issued"),
            ]
        )
        db.commit()
        ranking = rank_events(db, user)
    finally:
        db.close()

    top = ranking.suggestions[0]
    assert top.event.id == owned[3]
    assert top.signal == "cooccurrence"
    assert "also booked this" in top.reason
    # The named anchor is the event the follower actually attended, not the
    # caller's other booking.
    assert titles[owned[2]] in top.reason
    assert titles[owned[0]] not in top.reason


def test_a_sold_out_event_does_not_outrank_a_bookable_one() -> None:
    """A full event is not unregisterable, but it is not bookable either."""
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        clear_events(db)
        # A past booking here, so venue affinity matches both candidates equally.
        booked = Event(
            title="Recital Past",
            description="An earlier event at this venue.",
            date_time=datetime(2026, 1, 1, 18, 0),
            venue="Innovation Hall",
            capacity=50,
        )
        full = Event(
            title="Sold Out Gala",
            description="Every seat is already gone.",
            date_time=datetime(2027, 5, 1, 18, 0),
            venue="Innovation Hall",
            capacity=1,
        )
        open_event = Event(
            title="Open House",
            description="Seats are still available here.",
            date_time=datetime(2027, 5, 2, 18, 0),
            venue="Innovation Hall",
            capacity=50,
        )
        db.add_all([booked, full, open_event])
        db.commit()
        booked_id, full_id, open_id = booked.id, full.id, open_event.id

        owned_ticket(db, user, booked_id, campus_id="CAMP-VENUE")
        crowd_ticket(db, full_id, campus_id="CAMP-GALA")
        db.commit()

        ranking = rank_events(db, user)
    finally:
        db.close()

    order = [item.event.id for item in ranking.suggestions]
    assert order.index(open_id) < order.index(full_id)
    # Still offered, since joining the waitlist is a real action.
    assert full_id in order


def test_ordering_is_stable_between_identical_calls() -> None:
    reset_database()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        add_events(db, 5)
        first = [item.event.id for item in rank_events(db, user).suggestions]
        second = [item.event.id for item in rank_events(db, user).suggestions]
    finally:
        db.close()

    assert first == second


# --- the endpoint ---------------------------------------------------------


def test_suggestions_require_a_signed_in_user() -> None:
    reset_database()
    client = TestClient(app)
    assert client.get("/me/event-suggestions").status_code == 401


def test_suggestions_reject_the_admin_role() -> None:
    """Only attendees get recommendations; an admin workspace has no history."""
    reset_database()
    client = TestClient(app)
    headers = auth_headers(client, "admin", "admin123")
    assert client.get("/me/event-suggestions", headers=headers).status_code == 403


def test_endpoint_degrades_to_rule_copy_without_a_key(monkeypatch) -> None:
    """No API key is not an error: the row is still fully populated."""
    reset_database()
    monkeypatch.setattr(groq, "is_configured", lambda: False)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "")

    db = SessionLocal()
    try:
        clear_events(db)
        add_events(db, 2)
    finally:
        db.close()

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    body = client.get("/me/event-suggestions", headers=headers).json()

    assert body["ai_enabled"] is False
    assert len(body["items"]) == 2
    assert all(item["source"] == "rule" for item in body["items"])
    assert all(item["reason"] for item in body["items"])


def test_endpoint_reuses_cached_copy_and_never_waits_on_the_model(monkeypatch) -> None:
    """Second call serves the cache, so no request pays the model's latency."""
    reset_database()

    db = SessionLocal()
    try:
        clear_events(db)
        add_events(db, 2)
    finally:
        db.close()

    calls: list[int] = []

    def counting_post(*args, **kwargs):
        calls.append(1)
        return fake_completion()(*args, **kwargs)

    monkeypatch.setattr(groq, "is_configured", lambda: True)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq.httpx, "post", counting_post)

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")

    first = client.get("/me/event-suggestions", headers=headers).json()
    assert first["ai_enabled"] is True
    # Cold cache: rule copy now, model queued behind the response.
    assert all(item["source"] == "rule" for item in first["items"])
    assert len(calls) == 1

    second = client.get("/me/event-suggestions", headers=headers).json()
    assert all(item["source"] == "ai" for item in second["items"])
    assert all(item["reason"] == "Worth a look." for item in second["items"])
    # A warm cache means no second model call.
    assert len(calls) == 1


def test_a_changed_event_set_invalidates_the_cache(monkeypatch) -> None:
    """Copy written about one state of the data must not outlive it."""
    reset_database()

    db = SessionLocal()
    try:
        clear_events(db)
        events = add_events(db, 2)
    finally:
        db.close()
    event_ids = list(events)

    monkeypatch.setattr(groq, "is_configured", lambda: True)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq.httpx, "post", fake_completion("Stale wording."))

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    client.get("/me/event-suggestions", headers=headers)
    warm = client.get("/me/event-suggestions", headers=headers).json()
    assert all(item["source"] == "ai" for item in warm["items"])

    # Registering for one of the suggestions changes the caller's history, so the
    # digest changes and the cached wording is no longer trusted.
    client.post(
        "/tickets",
        json={"event_id": event_ids[0], "attendee_name": "Riya Sen", "attendee_contact": "riya@example.edu"},
        headers=headers,
    )

    after = client.get("/me/event-suggestions", headers=headers).json()
    assert all(item["source"] == "rule" for item in after["items"])
    assert event_ids[0] not in {item["event"]["id"] for item in after["items"]}


def test_unknown_event_ids_from_the_model_are_dropped(monkeypatch) -> None:
    """A hallucinated id must not become a suggestion."""
    reset_database()

    db = SessionLocal()
    try:
        clear_events(db)
        events = add_events(db, 2)
    finally:
        db.close()
    expected = set(events)

    import json as json_module

    class Invented:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": json_module.dumps(
                {"reasons": [{"event_id": 9999, "reason": "An event that does not exist."}]}
            )}}]}

    monkeypatch.setattr(groq, "is_configured", lambda: True)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq.httpx, "post", lambda *a, **k: Invented())

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    client.get("/me/event-suggestions", headers=headers)
    body = client.get("/me/event-suggestions", headers=headers).json()

    assert all(item["source"] == "rule" for item in body["items"])
    assert {item["event"]["id"] for item in body["items"]} == expected


def test_a_model_failure_falls_back_to_rule_copy(monkeypatch) -> None:
    reset_database()
    db = SessionLocal()
    try:
        clear_events(db)
        add_events(db, 2)
    finally:
        db.close()

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(groq, "is_configured", lambda: True)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq.httpx, "post", boom)

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    body = client.get("/me/event-suggestions", headers=headers).json()

    assert len(body["items"]) == 2
    assert all(item["source"] == "rule" for item in body["items"])


# --- the privacy boundary -------------------------------------------------


def test_the_prompt_carries_no_attendee_personal_data(monkeypatch) -> None:
    """The strongest guarantee in this feature, asserted against the real payload.

    A regression guard for something invisible in review: if a future edit adds
    the attendee's name, email, or campus id to the prompt, personal data would
    leave the process with no other signal to catch it.
    """
    reset_database()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "attendee").one()
        events = add_events(db, 2)
        owned_ticket(db, user, events[0], tier="vip", campus_id="CAMP-SECRET")
        attendee = db.query(Attendee).filter(Attendee.campus_id == "CAMP-SECRET").one()
        attendee.name = "Priya Chatterjee"
        attendee.contact_email = "priya.chatterjee@secret-college.edu"
        db.commit()
    finally:
        db.close()

    captured: dict = {}

    def capture(url, *, headers, json, timeout):
        captured["prompt"] = json["messages"]
        captured["auth"] = headers.get("Authorization", "")
        raise httpx.ConnectError("captured")

    monkeypatch.setattr(groq, "is_configured", lambda: True)
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq.httpx, "post", capture)

    client = TestClient(app)
    headers = auth_headers(client, "attendee", "attendee123")
    # The refresh is a background task, which TestClient runs inside the request,
    # so this exercises the real code path rather than a stand-in.
    client.get("/me/event-suggestions", headers=headers)

    assert "prompt" in captured, "the model was never called, so nothing was verified"
    serialised = " ".join(message["content"] for message in captured["prompt"]).lower()

    # The values that would actually identify someone. The word "attendee" is
    # deliberately not in this list: it is generic vocabulary in the prompt
    # instructions, and banning it would be asserting on our own wording rather
    # than on what the payload carries.
    for secret in ("priya", "chatterjee", "priya.chatterjee@secret-college.edu", "campus-secret", "student user"):
        assert secret not in serialised, f"prompt leaked {secret!r}"

    # The key travels as a bearer token, never inside the prompt body.
    assert captured["auth"] == "Bearer test-key"
    assert "test-key" not in serialised


def test_the_prompt_forbids_unsupported_popularity_claims() -> None:
    """A live model described a zero-registration event as "popular among
    attendees" and "draws many enthusiastic participants". The prompt was asking
    for it. These assertions pin the fix: real counts go in, and the model is told
    not to reach for popularity language it cannot support.
    """
    messages = groq._build_prompt([prompt_item(1, registered=0)], cold_start=True)
    system = messages[0]["content"].lower()
    user = messages[-1]["content"].lower()

    # The real count reaches the model, so it can reason about it.
    assert "registered: 0" in user
    # And it is told not to imply popularity when that count is zero.
    assert "above zero" in system
    assert "popular" in system
    # A cold start must not be described as a personal match.
    assert "none of these is a personal match" in user
    assert "popular with other attendees" not in user

    # With registrations, the count is still reported rather than assumed.
    assert "registered: 4" in groq._build_prompt([prompt_item(1, registered=4)], cold_start=True)[-1]["content"].lower()


def test_write_reasons_returns_empty_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "")

    def never_called(*_args, **_kwargs):
        raise AssertionError("must not call the model without a key")

    monkeypatch.setattr(groq.httpx, "post", never_called)
    assert groq.write_reasons(1, "sig", [prompt_item()], cold_start=True) == {}


def test_parser_tolerates_reasoning_preamble_and_rejects_junk() -> None:
    good = groq._parse_payload(
        'Thinking about it first...\n{"reasons": [{"event_id": 4, "reason": "  Popular   so far.  "},'
        ' {"event_id": "5", "reason": "Also good."}]}'
    )
    assert good == {4: "Popular so far.", 5: "Also good."}

    # Usable entries survive alongside unusable ones rather than being lost.
    mixed = groq._parse_payload(
        '{"reasons": [{"event_id": 1, "reason": "Fine."}, {"event_id": 2},'
        ' {"event_id": 3, "reason": ""}, {"event_id": 4, "reason": "' + "x" * 300 + '"}]}'
    )
    assert mixed == {1: "Fine."}

    for junk in ("", "not json at all", "{broken", '{"reasons": "nope"}', '{"reasons": [1, 2]}'):
        assert groq._parse_payload(junk) is None


def test_a_retired_model_falls_back_once(monkeypatch) -> None:
    """A 404 on the configured model is a settings problem, not a crash."""
    attempted: list[str] = []

    def respond(model, **_kwargs):
        attempted.append(model)
        if model == "retired-model":
            return 404, "model_not_found"
        return 200, '{"reasons": [{"event_id": 1, "reason": "Good pick."}]}'

    monkeypatch.setattr(groq, "get_groq_api_key", lambda: "test-key")
    monkeypatch.setattr(groq, "get_groq_model", lambda: "retired-model")
    monkeypatch.setattr(groq, "get_groq_fallback_model", lambda: "good-model")
    monkeypatch.setattr(groq, "_request", lambda model, messages, timeout: respond(model))

    assert groq.write_reasons(1, "sig", [prompt_item()], cold_start=True) == {1: "Good pick."}
    assert attempted == ["retired-model", "good-model"]
