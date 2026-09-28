"""Deterministic event ranking for the attendee's "Recommended for you" row.

Nothing in here talks to a model or to the network. The LLM in ``groq.py`` only
rewrites the copy for events this module has already chosen, so a model outage
changes the wording and never the selection.

Two independent sources of signal are scored and added together, because either
can be empty:

* **Own history** -- what this account registered for. Keyed on ``user_id``.
  Empty for most accounts today, because tickets issued before ``tickets.user_id``
  existed recorded no owner.
* **Crowd** -- what every attendee registers for. Keyed on ``attendee_id`` rather
  than ``user_id`` on purpose: that is the durable person identity, so it still
  works for the pre-existing tickets that have no owning account.

That split is what lets the feature be useful on a cold database: with no
personal history the crowd terms still rank something, and the copy says so
plainly rather than pretending to know the attendee.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import re

from sqlalchemy import select
from sqlalchemy.orm import Session, subqueryload

from .models import Event, Ticket, User, WaitlistEntry


# Tuned by hand and kept in one place. Own-history terms outweigh crowd terms
# because a personal match is a stronger signal than a popular one.
SUGGESTION_WEIGHTS = {
    "own_tier": 3.0,
    "own_venue": 2.0,
    "own_topic": 2.0,
    "cooccurrence": 2.5,
    "crowd_popularity": 1.0,
}

#: Multiplier applied to every personal signal. A full event is not
#: unregisterable -- the waitlist is a real action -- but it must never outrank
#: something the attendee could actually book, so the two groups are ordered
#: separately rather than blended with a fudge factor. A fudge factor did not work:
#: a full event attracts registrations, which feeds it more signals, so a penalty
#: large enough to matter had to be large enough to distort the bookable group too.
SOLD_OUT_SORT_KEY = 1

MAX_SUGGESTIONS = 3

# A word shorter than this, or in this list, carries no topic signal.
_MIN_TOKEN_LENGTH = 4
_STOPWORDS = frozenset(
    """
    about above after again against also among annual around because been before
    being below between both campus during each evening event events every from
    further have having here into more most other over same shall since some
    such than that their them then there these they this those through under
    until very were what when where which while with would your
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(*values: str) -> set[str]:
    """Lowercase word tokens with stopwords and noise removed."""
    found: set[str] = set()
    for value in values:
        for token in _TOKEN_RE.findall((value or "").lower()):
            if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOPWORDS:
                found.add(token)
    return found


@dataclass(frozen=True)
class Suggestion:
    """One ranked event and the reason it was picked."""

    event: Event
    score: float
    signal: str
    reason: str
    source: str = "rule"


@dataclass(frozen=True)
class Ranking:
    suggestions: list[Suggestion]
    cold_start: bool
    #: Events this account already holds a live ticket or queue place for.
    registered_event_ids: set[int]
    #: Fills the row when scoring cannot separate anything, so an attendee with
    #: nothing to go on still gets real, registrable events instead of a blank.
    fallback_used: bool = False


def _registration_events(db: Session, user: User) -> tuple[list[int], Counter, Counter, set[str], set[int]]:
    """The caller's own history.

    Returns the event ids, tier mix, venue mix, topic tokens, and the attendee ids
    behind them. The attendee ids matter because the caller must not be counted as
    one of their own peers in the co-occurrence denominator.
    """
    ticket_rows = (
        db.query(Ticket)
        .filter(Ticket.user_id == user.id, Ticket.status != "revoked")
        .all()
    )
    waitlist_rows = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.user_id == user.id, WaitlistEntry.status.in_(("waiting", "promoted")))
        .all()
    )

    event_ids: set[int] = set()
    tiers: Counter = Counter()
    venues: Counter = Counter()
    tokens: set[str] = set()
    attendee_ids: set[int] = set()

    for ticket in ticket_rows:
        event_ids.add(ticket.event_id)
        tiers[ticket.tier] += 1
        attendee_ids.add(ticket.attendee_id)
        event = ticket.event
        if event is not None:
            venues[event.venue] += 1
            tokens |= _tokens(event.title, event.description)

    for entry in waitlist_rows:
        event_ids.add(entry.event_id)
        tiers[entry.tier] += 1
        if entry.attendee_id is not None:
            attendee_ids.add(entry.attendee_id)
        event = entry.event
        if event is not None:
            venues[event.venue] += 1
            tokens |= _tokens(event.title, event.description)

    return sorted(event_ids), tiers, venues, tokens, attendee_ids


def _crowd_index(db: Session) -> tuple[dict[int, set[int]], Counter, dict[int, Counter], Counter]:
    """Registration history grouped by person, plus popularity per event and tier.

    Keyed on ``attendee_id`` because that is the identity that survives a missing
    ``tickets.user_id``, so legacy rows still contribute.
    """
    by_attendee: dict[int, set[int]] = {}
    popularity: Counter = Counter()
    event_tiers: dict[int, Counter] = {}
    tier_popularity: Counter = Counter()

    ticket_rows = (
        db.query(Ticket)
        .filter(Ticket.status != "revoked", Ticket.attendee_id.isnot(None))
        .all()
    )
    for ticket in ticket_rows:
        by_attendee.setdefault(ticket.attendee_id, set()).add(ticket.event_id)
        popularity[ticket.event_id] += 1
        event_tiers.setdefault(ticket.event_id, Counter())[ticket.tier] += 1
        tier_popularity[ticket.tier] += 1

    waitlist_rows = (
        db.query(WaitlistEntry)
        .filter(WaitlistEntry.status == "waiting", WaitlistEntry.attendee_id.isnot(None))
        .all()
    )
    for entry in waitlist_rows:
        by_attendee.setdefault(entry.attendee_id, set()).add(entry.event_id)
        popularity[entry.event_id] += 1
        event_tiers.setdefault(entry.event_id, Counter())[entry.tier] += 1
        tier_popularity[entry.tier] += 1

    return by_attendee, popularity, event_tiers, tier_popularity


@dataclass(frozen=True)
class Cooccurrence:
    """What the shared-booking signal found for one candidate."""

    #: Share of the caller's peers who also booked the candidate, 0..1.
    share: float
    #: How many of those peers there were.
    peers: int
    #: The caller's own event that those peers turned up at.
    #: Needed because the reason names it, and naming the wrong one would state
    #: something false about who went where.
    anchor_event_id: int | None = None


def _cooccurrence(
    own_event_ids: list[int],
    candidate_id: int,
    by_attendee: dict[int, set[int]],
    exclude_attendee_ids: set[int] | None = None,
) -> Cooccurrence:
    """Of the people who shared the caller's events, the share who also booked this.

    Measured as a conditional share rather than a raw count, so it does not decay
    as the crowd grows: with two peers who both followed the caller to Poetry Slam
    the value is 1.0 whether the database holds four registrations or four
    thousand. Counting peers against the total crowd instead made this signal
    unreadable on any dataset with a long tail of one-off registrations.

    ``exclude_attendee_ids`` drops the caller from the peer pool. They share their
    own events by definition, so leaving them in inflated the denominator and
    quietly halved this signal.
    """
    if not own_event_ids:
        return Cooccurrence(0.0, 0)

    excluded = exclude_attendee_ids or set()
    peer_sets = [
        events
        for attendee_id, events in by_attendee.items()
        if attendee_id not in excluded and any(event_id in events for event_id in own_event_ids)
    ]
    if not peer_sets:
        return Cooccurrence(0.0, 0)

    followers = [events for events in peer_sets if candidate_id in events]
    if not followers:
        return Cooccurrence(0.0, 0)

    # The anchor is an event the caller and its followers genuinely both booked,
    # never just the caller's first event.
    own = set(own_event_ids)
    shared_own = [
        event_id
        for event_id in sorted(own)
        if any(event_id in events for events in followers)
    ]
    return Cooccurrence(
        # Damped by peer count for the same reason tier affinity is: one follower
        # is not proof that "the people who came with you" came with you.
        share=(len(followers) / len(peer_sets)) * _confidence(len(followers)),
        peers=len(followers),
        anchor_event_id=shared_own[0] if shared_own else None,
    )


def _normalise(value: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    return min(value / ceiling, 1.0)


def _confidence(observations: int) -> float:
    """How much a personal signal built from ``n`` observations should count for.

    n / (n + 1), which approaches 1 and never reaches it. Without this, a single
    registration scored the same as five, so "You usually pick general seating"
    could be said on the strength of one general booking. Preference needs
    evidence, and this is the cheapest honest way to express that.
    """
    if observations <= 0:
        return 0.0
    return observations / (observations + 1)


def _reason_for(
    signal: str,
    candidate: Event,
    *,
    matched_tier: str | None = None,
    peer_count: int = 0,
    popularity: int = 0,
    anchor_title: str | None = None,
) -> str:
    """The copy shown when no model wrote anything.

    Each branch names the signal that actually won the score, so the sentence
    cannot claim a personal connection the data does not support. In particular
    a cold start can only ever produce the crowd or fallback phrasing.
    """
    if signal == "cooccurrence" and anchor_title:
        who = "1 other attendee" if peer_count == 1 else f"{peer_count} other attendees"
        return f"{who} who booked {anchor_title} also booked this."
    if signal == "own_tier" and matched_tier:
        return f"You usually pick {matched_tier} seating."
    if signal == "own_venue":
        return f"Also at {candidate.venue}, where you have been before."
    if signal == "own_topic" and anchor_title:
        return f"Similar to {anchor_title}, which you registered for."
    if popularity > 0:
        who = "1 other attendee has" if popularity == 1 else f"{popularity} other attendees have"
        return f"Popular right now - {who} already registered."
    return f"{max(candidate.capacity - candidate.issued_count, 0)} seats left at {candidate.venue}."


def signal_label(signal: str) -> str:
    """A stable, non-identifying label for why an event was chosen.

    Useful for debugging why the row looks the way it does, and safe to log: it
    describes the signal, never the attendee.
    """
    return {
        "own_tier": "your tier preference",
        "own_venue": "a venue you have used",
        "own_topic": "similar to your events",
        "cooccurrence": "attendees who shared your events",
        "crowd_popularity": "popular with other attendees",
    }.get(signal, "seats remaining")


def rank_events(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> Ranking:
    """Rank registrable events for one attendee."""
    moment = now or datetime.now()
    own_event_ids, own_tiers, own_venues, own_tokens, own_attendee_ids = _registration_events(db, user)
    by_attendee, popularity, event_tiers, tier_popularity = _crowd_index(db)

    # tickets are eager-loaded because EventOut and the prompt both read
    # tier_counts, which is a property over self.tickets and would otherwise
    # lazy-load per event.
    all_events = (
        db.execute(
            select(Event).options(subqueryload(Event.tickets)).order_by(Event.date_time.asc())
        )
        .scalars()
        .all()
    )

    # Anything the caller can no longer act on is not a candidate. Their own past
    # events are excluded even when the ticket is revoked, because the history is
    # still a real signal and re-issuing is handled by the 409 in issue_ticket.
    candidates = [
        event
        for event in all_events
        if event.id not in own_event_ids and event.date_time > moment
    ]

    if not candidates:
        return Ranking([], cold_start=not own_event_ids, registered_event_ids=set(own_event_ids))

    max_popularity = max((popularity.get(event.id, 0) for event in candidates), default=0)
    # A tier signal is only worth anything if it *distinguishes* the caller from
    # everybody else. Comparing against the crowd's own tier mix means a caller who
    # always takes "general" scores nothing for it: general is the default, so
    # choosing it is the absence of a choice rather than a preference. That also
    # keeps "You usually pick general seating" -- a true but meaningless thing to
    # say -- from outranking a signal that actually says something.
    own_total = sum(own_tiers.values()) or 1
    crowd_total = sum(tier_popularity.values()) or 1
    own_confidence = _confidence(own_total)
    max_own_venue = max(own_venues.values(), default=0)

    # Fetched once: the anchor title is needed for several candidates and a query
    # per candidate would be wasted work on the hottest path in the browse view.
    own_events = db.query(Event).filter(Event.id.in_(own_event_ids)).all() if own_event_ids else []
    own_event_tokens = {event.id: _tokens(event.title, event.description) for event in own_events}
    titles_by_id = {event.id: event.title for event in own_events}

    def best_anchor(overlap: set[str]) -> str | None:
        if not own_events:
            return None
        if overlap:
            return max(own_events, key=lambda event: len(overlap & own_event_tokens[event.id])).title
        return own_events[0].title

    scored: list[Suggestion] = []
    for event in candidates:
        parts: dict[str, float] = {}

        # A tier preference only counts when this event has actually offered that
        # tier, and only to the extent the caller over-selects it. Events have no
        # tier of their own -- tiers belong to tickets -- so the match is between
        # what the caller picks and what was issued here.
        matched_tier: str | None = None
        matched_tier_score = 0.0
        for tier in event_tiers.get(event.id, {}):
            if tier not in own_tiers:
                continue
            excess = (own_tiers[tier] / own_total) - (tier_popularity.get(tier, 0) / crowd_total)
            if excess > matched_tier_score:
                matched_tier_score, matched_tier = excess, tier
        if matched_tier:
            parts["own_tier"] = min(matched_tier_score, 1.0) * own_confidence

        if own_venues.get(event.venue):
            parts["own_venue"] = _normalise(own_venues[event.venue], max_own_venue) * own_confidence

        overlap = own_tokens & _tokens(event.title, event.description)
        if overlap:
            parts["own_topic"] = _normalise(len(overlap), max(len(own_tokens), 1))

        cooc = _cooccurrence(own_event_ids, event.id, by_attendee, own_attendee_ids)
        if cooc.share > 0:
            parts["cooccurrence"] = cooc.share

        pop = popularity.get(event.id, 0)
        if pop:
            parts["crowd_popularity"] = _normalise(pop, max_popularity)

        score = sum(SUGGESTION_WEIGHTS[name] * value for name, value in parts.items())

        # The signal that actually won is the largest *weighted* contribution,
        # which is what the copy has to describe.
        signal = max(parts, key=lambda name: SUGGESTION_WEIGHTS[name] * parts[name]) if parts else "none"

        # Each reason names the event that actually justifies it. The co-occurrence
        # anchor comes from the peers themselves, so the sentence cannot credit the
        # caller's book for a turnout that did not happen.
        if signal == "cooccurrence":
            anchor = titles_by_id.get(cooc.anchor_event_id) if cooc.anchor_event_id else None
            anchor_title = anchor or "an event you registered for"
        elif signal == "own_topic":
            anchor_title = best_anchor(overlap)
        else:
            anchor_title = None

        reason = _reason_for(
            signal,
            event,
            matched_tier=matched_tier,
            peer_count=cooc.peers,
            popularity=pop,
            anchor_title=anchor_title,
        )
        scored.append(Suggestion(event=event, score=round(score, 4), signal=signal, reason=reason))

    # Bookable events come first as a group, whatever the score. date_time then id
    # keeps the order stable between identical requests, so the row does not
    # reshuffle between two calls at the same seat count.
    scored.sort(
        key=lambda suggestion: (
            SOLD_OUT_SORT_KEY if max(suggestion.event.capacity - suggestion.event.issued_count, 0) == 0 else 0,
            -suggestion.score,
            suggestion.event.date_time,
            suggestion.event.id,
        )
    )
    return Ranking(
        suggestions=scored[:limit],
        cold_start=not own_event_ids,
        registered_event_ids=set(own_event_ids),
    )
