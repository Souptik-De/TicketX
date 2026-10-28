from datetime import datetime

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=3, max_length=120)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=3, max_length=120)
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["user", "scanner"] = Field(default="user")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    display_name: str
    # Only ever populated for Google sign-ins, where the provider supplies it.
    # The registration form prefills from this and leaves the field blank
    # otherwise, so a password account is never assumed to own an address.
    email: str | None = None


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=10, max_length=8000)
    role: Literal["user", "scanner"] = Field(default="user")


class TierCount(BaseModel):
    tier: str
    issued_count: int


class EventTierOut(BaseModel):
    """Sellable-seat totals for one tier of a seat-map event.

    Blocked seats are deliberately excluded, so the tier totals sum to the
    event capacity and ``available`` can be compared directly with ``total``.
    """

    name: str
    total: int
    available: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    date_time: datetime
    venue: str
    artist: str | None = None
    city: str | None = None
    category: str | None = None
    capacity: int
    issued_count: int
    revoked_count: int
    tier_counts: list[TierCount]
    has_seat_map: bool = False
    is_cancelled: bool = False
    # Filled by the event listing for seat-map events; legacy events stay empty.
    tiers: list[EventTierOut] = Field(default_factory=list)


class EventSearchResponse(BaseModel):
    items: list[EventOut]
    total: int
    page: int
    page_size: int


class EventFacetsOut(BaseModel):
    categories: list[str] = []
    cities: list[str] = []


class RowIn(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    blocks: list[Annotated[int, Field(strict=True, ge=1)]] = Field(min_length=1)
    blocked: list[Annotated[int, Field(strict=True, ge=1)]] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        label = value.strip()
        if not label:
            raise ValueError("row label must not be empty")
        if not label.isalnum():
            raise ValueError("row label may contain only letters and digits")
        return label

    @model_validator(mode="after")
    def _validate_blocked_seats(self) -> "RowIn":
        total = sum(self.blocks)
        for number in self.blocked:
            if number > total:
                raise ValueError(
                    f"blocked seat numbers for row {self.label!r} must be between 1 and {total}"
                )
        return self


class TierIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    rows: list[RowIn] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("tier name must not be empty")
        return name


class SeatLayoutIn(BaseModel):
    tiers: list[TierIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_layout(self) -> "SeatLayoutIn":
        if len(self.tiers) > 10:
            raise ValueError("an event may have at most 10 tiers")

        tier_names = [tier.name.casefold() for tier in self.tiers]
        if len(set(tier_names)) != len(tier_names):
            raise ValueError("tier names must be unique (case-insensitive)")

        rows = [row for tier in self.tiers for row in tier.rows]
        if len(rows) > 60:
            raise ValueError("an event may have at most 60 rows")

        row_labels = [row.label.casefold() for row in rows]
        if len(set(row_labels)) != len(row_labels):
            raise ValueError("row labels must be unique across the event (case-insensitive)")

        total_seats = sum(sum(row.blocks) for row in rows)
        if total_seats > 2000:
            raise ValueError("an event may have at most 2000 seats")

        # Row labels may themselves end in digits, so "A1" seat 2 and "A" seat 12
        # both produce "A12". The map would render two indistinguishable seats.
        generated_labels: set[str] = set()
        for row in rows:
            seat_count = sum(row.blocks)
            for seat_number in range(1, seat_count + 1):
                label = f"{row.label}{seat_number}"
                if label in generated_labels:
                    raise ValueError(
                        f"seat label {label!r} is generated more than once; "
                        "row labels and seat counts must not collide"
                    )
                generated_labels.add(label)

        return self


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=600)
    date_time: datetime
    venue: str = Field(min_length=2, max_length=160)
    artist: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    # Capacity is required for legacy events. A seat-map event derives it from
    # the sellable seats in the layout, so any value sent alongside is ignored.
    capacity: int | None = None
    seat_layout: SeatLayoutIn | None = None

    @field_validator("artist", "city", "category")
    @classmethod
    def _normalise_metadata(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @model_validator(mode="after")
    def _validate_capacity(self) -> "EventCreate":
        if self.seat_layout is None:
            if self.capacity is None:
                raise ValueError("capacity is required when no seat_layout is provided")
            if not 1 <= self.capacity <= 50000:
                raise ValueError("capacity must be greater than 0 and at most 50000")
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    date_time: datetime | None = None
    venue: str | None = Field(default=None, min_length=2, max_length=160)
    artist: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    capacity: int | None = Field(default=None, ge=1, le=50000)
    is_cancelled: bool | None = None

    @field_validator("artist", "city", "category")
    @classmethod
    def _normalise_metadata(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @model_validator(mode="after")
    def _reject_explicit_nulls(self) -> "EventUpdate":
        required_fields = (
            "title",
            "description",
            "date_time",
            "venue",
            "capacity",
            "is_cancelled",
        )
        nulls = [
            name
            for name in required_fields
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if nulls:
            raise ValueError(f"{', '.join(nulls)} must not be null")
        return self


class SeatOut(BaseModel):
    id: int
    number: int
    label: str
    state: Literal["available", "held", "booked", "blocked"]
    mine: bool = False


class RowOut(BaseModel):
    label: str
    blocks: list[list[SeatOut]]


class TierMapOut(BaseModel):
    name: str
    rows: list[RowOut]


class SeatMapOut(BaseModel):
    event_id: int
    tiers: list[TierMapOut]
    total: int
    available: int
    held: int
    booked: int


class AttendeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    campus_id: str
    contact_email: EmailStr


class TicketCreate(BaseModel):
    event_id: int
    attendee_name: str = Field(min_length=2, max_length=120)
    attendee_contact: EmailStr
    campus_id: str | None = Field(default=None, max_length=80)
    tier: str = Field(default="general", max_length=40)


class TicketCreated(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome: Literal["ticketed"] = "ticketed"
    ticket_id: int
    qr_signature: str
    tier: str
    seat_number: str
    status: str


class WaitlistJoinResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    outcome: Literal["waitlisted"] = "waitlisted"
    waitlisted: bool = True
    position: int
    event_id: int
    waitlist_entry_id: int


TicketIssuanceResponse = TicketCreated | WaitlistJoinResponse


class TicketDetail(BaseModel):
    ticket_id: int
    qr_signature: str
    tier: str
    seat_number: str
    status: str
    event: EventOut
    attendee: AttendeeOut


class PromotedAttendeeInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attendee_name: str
    new_ticket_id: int
    tier: str


class HoldCreate(BaseModel):
    """Seats to claim. The 1..6 rule is enforced in ``create_hold`` so a bad
    count is a 400 with a readable message rather than a Pydantic 422."""

    seat_ids: list[int]


class HeldSeatOut(BaseModel):
    id: int
    label: str
    tier_name: str


class HoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hold_id: int
    event_id: int
    status: str
    seconds_remaining: int
    # Populated while the hold is active, and on the cancel response for the
    # seats it just released. Empty once the link is gone, because a released
    # seat keeps no record of the hold it used to belong to.
    seats: list[HeldSeatOut] = Field(default_factory=list)


class ConfirmHoldIn(BaseModel):
    attendee_name: str = Field(min_length=2, max_length=120)
    attendee_contact: EmailStr
    campus_id: str | None = Field(default=None, max_length=80)


class ConfirmHoldOut(BaseModel):
    hold_id: int
    status: str
    tickets: list[TicketDetail] = Field(default_factory=list)


class EventWaitlistIn(BaseModel):
    attendee_name: str = Field(min_length=2, max_length=120)
    attendee_contact: EmailStr
    campus_id: str | None = Field(default=None, max_length=80)



class RevokeTicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket_id: int
    status: str
    revoked_at: datetime | None = None
    promoted_attendee: PromotedAttendeeInfo | None = None


class MyWaitlistEntry(BaseModel):
    """One of the caller's own queue places, as shown to the attendee.

    ``position`` is the live queue position while waiting and ``None`` once the
    entry has been promoted or withdrawn. ``promoted_ticket`` is what lets the
    promoted attendee reach the ticket ET-11 issued them without a second guess
    at an identifier.
    """

    waitlist_entry_id: int
    event_id: int
    event_title: str
    event_venue: str
    event_date_time: datetime
    attendee_name: str
    attendee_contact: str
    campus_id: str
    tier: str
    position: int | None
    status: str
    created_at: datetime
    promoted_ticket: TicketDetail | None = None


class MyRegistrations(BaseModel):
    """Everything the caller holds, both halves of it.

    The user workspace polls this, so ``waitlist`` and ``tickets`` arrive
    together: the first drives the live queue position and the promotion notice,
    the second is the "my tickets" list that survives a reload.
    """

    waitlist: list[MyWaitlistEntry] = []
    tickets: list[TicketDetail] = []


class EventSuggestion(BaseModel):
    """One recommended event, with the reason it was chosen.

    The event is a full ``EventOut`` so the UI can render the same card the
    catalog uses. ``source`` says whether ``reason`` came from the model or from
    the deterministic rules, which is useful when a model call fails and the
    wording visibly changes.
    """

    event: EventOut
    reason: str
    source: Literal["ai", "rule"] = "rule"
    signal: str
    score: float


class EventSuggestionOut(BaseModel):
    """The "Recommended for you" row.

    ``cold_start`` is true when the caller has no registration history, so the
    copy is crowd-based rather than personal. ``ai_enabled`` is false when no
    API key is configured, in which case every reason is rule-based and the row
    is still fully populated.
    """

    items: list[EventSuggestion] = []
    cold_start: bool = True
    ai_enabled: bool = False
    generated_at: datetime | None = None





class ScanCreate(BaseModel):
    ticket_id: int | None = None
    qr_signature: str | None = None
    gate_id: int
    volunteer_id: int


class PriorScan(BaseModel):
    gate_name: str
    timestamp: datetime


class ScanResult(BaseModel):
    result: str
    message: str
    ticket_id: int | None = None
    attendee_name: str | None = None
    tier: str | None = None
    seat_number: str | None = None
    scanned_at: datetime | None = None
    prior_scan: PriorScan | None = None


class MyScanItem(BaseModel):
    scan_id: int
    timestamp: datetime
    result: str
    ticket_id: int
    attendee_name: str
    tier: str
    seat_number: str
    gate_name: str
    gate_location: str
    event_title: str


class MyScanGate(BaseModel):
    gate_id: int
    name: str
    location: str
    scanned_count: int


class MyScanStats(BaseModel):
    total: int
    valid: int
    duplicate: int
    invalid: int
    gates: list[MyScanGate]
    recent: list[MyScanItem]


class MyTicketStatItem(BaseModel):
    ticket_id: int
    event_title: str
    event_date_time: datetime
    venue: str
    tier: str
    seat_number: str
    status: str
    checked_in: bool
    check_gate_name: str | None = None
    checked_at: datetime | None = None


class MyTicketTier(BaseModel):
    tier: str
    count: int


class MyTicketStats(BaseModel):
    total: int
    used: int
    valid: int
    waitlisted: int
    tiers: list[MyTicketTier]
    tickets: list[MyTicketStatItem]


class GateStatus(BaseModel):
    gate_id: int
    event_id: int | None
    name: str
    location: str
    online: bool
    scanned_count: int
    last_synced: datetime | None


class GateCreate(BaseModel):
    event_id: int
    name: str = Field(min_length=2, max_length=80)
    location: str = Field(min_length=2, max_length=160)
    volunteer_name: str | None = Field(default=None, max_length=120)


class GateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int | None
    name: str
    location: str


class VolunteerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    gate_id: int | None


class EventStatsTicket(BaseModel):
    ticket_id: int
    attendee_name: str
    campus_id: str
    contact_email: str
    tier: str
    seat_number: str
    status: str
    issued_at: datetime
    checked_in: bool
    check_gate_name: str | None = None
    check_gate_location: str | None = None
    checked_at: datetime | None = None
    checked_by: str | None = None


class EventStatsGate(BaseModel):
    gate_id: int
    name: str
    location: str
    scanned_count: int


class EventStatsTier(BaseModel):
    tier: str
    issued: int
    checked_in: int


class WaitlistEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    attendee_name: str
    attendee_contact: str
    campus_id: str | None = None
    tier: str
    position: int
    status: str
    created_at: datetime | None = None
    # Set once ET-11's revocation has issued this person's ticket, so an admin
    # can see that a seat really went to the waitlist and not into the void.
    promoted_ticket_id: int | None = None
    resolved_at: datetime | None = None


class EventStatsOut(BaseModel):
    event_id: int
    title: str
    venue: str
    date_time: datetime
    capacity: int
    issued: int
    checked_in: int
    remaining: int
    check_in_rate: float
    tier_breakdown: list[EventStatsTier]
    gate_breakdown: list[EventStatsGate]
    tickets: list[EventStatsTicket]
    waitlist: list[WaitlistEntryOut] = []
    promoted: list[WaitlistEntryOut] = []

