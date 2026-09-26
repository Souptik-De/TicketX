from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class TierCount(BaseModel):
    tier: str
    issued_count: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    date_time: datetime
    venue: str
    capacity: int
    issued_count: int
    tier_counts: list[TierCount]


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=600)
    date_time: datetime
    venue: str = Field(min_length=2, max_length=160)
    capacity: int = Field(gt=0, le=50000)


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
    ticket_id: int
    qr_signature: str
    tier: str
    seat_number: str
    status: str


class TicketDetail(BaseModel):
    ticket_id: int
    qr_signature: str
    tier: str
    seat_number: str
    status: str
    event: EventOut
    attendee: AttendeeOut


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
    prior_scan: PriorScan | None = None


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
