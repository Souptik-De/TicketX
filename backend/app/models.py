from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column("user_id", Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column("event_id", Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    venue: Mapped[str] = mapped_column(String(160), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)

    gates: Mapped[list["Gate"]] = relationship(back_populates="event")


class Gate(Base):
    __tablename__ = "gates"

    id: Mapped[int] = mapped_column("gate_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.event_id"), nullable=True)

    event: Mapped[Event | None] = relationship(back_populates="gates")
    volunteers: Mapped[list["Volunteer"]] = relationship(back_populates="gate")


class Volunteer(Base):
    __tablename__ = "volunteers"

    id: Mapped[int] = mapped_column("volunteer_id", Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    gate_id: Mapped[int | None] = mapped_column(ForeignKey("gates.gate_id"), nullable=True)

    gate: Mapped[Gate | None] = relationship(back_populates="volunteers")
