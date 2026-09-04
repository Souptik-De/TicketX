from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=3, max_length=120)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    display_name: str


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    date_time: datetime
    venue: str
    capacity: int


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    date_time: datetime
    venue: str = Field(min_length=2, max_length=160)
    capacity: int = Field(gt=0, le=50000)


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
