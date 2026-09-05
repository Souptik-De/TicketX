import { useState } from "react";
import { Armchair, CalendarPlus, DoorOpen, MapPin, Plus, UsersRound } from "lucide-react";

import { createEvent, createGate } from "../lib/api";

export function AdminPanel({ events, gates, selectedEventId, onEventChange, onEventCreated, onGateCreated }) {
  const [title, setTitle] = useState("Freshers Night 2026");
  const [description, setDescription] = useState(
    "An evening of live performances, student showcases, food stalls, and campus celebrations.",
  );
  const [venue, setVenue] = useState("Seminar Hall");
  const [dateTime, setDateTime] = useState("2026-09-18T18:30");
  const [capacity, setCapacity] = useState(250);
  const [gateName, setGateName] = useState("VIP Gate");
  const [gateLocation, setGateLocation] = useState("Auditorium east entrance");
  const [volunteerName, setVolunteerName] = useState("Gate Volunteer");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isSavingEvent, setIsSavingEvent] = useState(false);
  const [isSavingGate, setIsSavingGate] = useState(false);

  const selectedEvent = events.find((event) => event.id === Number(selectedEventId)) ?? events[0];
  const selectedEventGates = selectedEvent ? gates.filter((gate) => gate.event_id === selectedEvent.id) : [];
  const totalCapacity = events.reduce((sum, event) => sum + event.capacity, 0);
  const totalIssued = events.reduce((sum, event) => sum + event.issued_count, 0);
  const totalCheckedIn = gates.reduce((sum, gate) => sum + (gate.scanned_count || 0), 0);

  async function handleEventSubmit(eventObject) {
    eventObject.preventDefault();
    setError("");
    setMessage("");
    setIsSavingEvent(true);

    try {
      const created = await createEvent({
        title,
        description,
        venue,
        date_time: new Date(dateTime).toISOString(),
        capacity,
      });
      onEventCreated(created);
      setMessage(`${created.title} is ready for tickets and gates.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create event");
    } finally {
      setIsSavingEvent(false);
    }
  }

  async function handleGateSubmit(eventObject) {
    eventObject.preventDefault();
    if (!selectedEvent) {
      setError("Create or select an event before adding a gate.");
      return;
    }

    setError("");
    setMessage("");
    setIsSavingGate(true);

    try {
      const created = await createGate({
        event_id: selectedEvent.id,
        name: gateName,
        location: gateLocation,
        volunteer_name: volunteerName,
      });
      onGateCreated(created);
      setMessage(`${created.name} added to ${selectedEvent.title}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create gate");
    } finally {
      setIsSavingGate(false);
    }
  }

  return (
    <section className="admin-grid">
      <div className="left-stack">
        <div className="panel admin-form-panel">
          <div className="section-heading">
            <p className="eyebrow">Admin Panel</p>
            <h2>Create Event</h2>
          </div>
          <form className="ticket-form" onSubmit={handleEventSubmit}>
            <label className="full-width">
              Event title
              <input value={title} onChange={(event) => setTitle(event.target.value)} required minLength={3} />
            </label>
            <label className="full-width">
              Event description
              <textarea
                rows={4}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What can attendees expect?"
              />
            </label>
            <label>
              Venue
              <input value={venue} onChange={(event) => setVenue(event.target.value)} required />
            </label>
            <label>
              Capacity
              <input
                type="number"
                min={1}
                value={capacity}
                onChange={(event) => setCapacity(Number(event.target.value))}
                required
              />
            </label>
            <label className="full-width">
              Date and time
              <input type="datetime-local" value={dateTime} onChange={(event) => setDateTime(event.target.value)} required />
            </label>
            <button className="primary-button" disabled={isSavingEvent} type="submit">
              <CalendarPlus size={18} aria-hidden="true" />
              {isSavingEvent ? "Creating..." : "Create event"}
            </button>
          </form>
        </div>

        <div className="panel admin-form-panel">
          <div className="section-heading">
            <p className="eyebrow">Gate Setup</p>
            <h2>Add Gate</h2>
          </div>
          <form className="ticket-form" onSubmit={handleGateSubmit}>
            <label className="full-width">
              Event
              <select value={selectedEvent?.id ?? ""} onChange={(event) => onEventChange(Number(event.target.value))} required>
                {events.map((event) => (
                  <option key={event.id} value={event.id}>
                    {event.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Gate name
              <input value={gateName} onChange={(event) => setGateName(event.target.value)} required minLength={2} />
            </label>
            <label>
              Volunteer
              <input value={volunteerName} onChange={(event) => setVolunteerName(event.target.value)} />
            </label>
            <label className="full-width">
              Location
              <input value={gateLocation} onChange={(event) => setGateLocation(event.target.value)} required minLength={2} />
            </label>
            {error && <p className="error-text">{error}</p>}
            {message && <p className="success-text">{message}</p>}
            <button className="primary-button" disabled={isSavingGate || !selectedEvent} type="submit">
              <Plus size={18} aria-hidden="true" />
              {isSavingGate ? "Adding..." : "Add gate"}
            </button>
          </form>
        </div>
      </div>

      <div className="right-stack">
        <div className="panel">
          <div className="section-heading">
            <p className="eyebrow">Organizer View</p>
            <h2>Events</h2>
          </div>
          <div className="admin-metrics">
            <div>
              <UsersRound size={20} aria-hidden="true" />
              <span>Total capacity</span>
              <strong>{totalCapacity}</strong>
            </div>
            <div>
              <MapPin size={20} aria-hidden="true" />
              <span>Checked in</span>
              <strong>{totalCheckedIn}</strong>
            </div>
            <div>
              <Armchair size={20} aria-hidden="true" />
              <span>Seats issued</span>
              <strong>{totalIssued}</strong>
            </div>
          </div>
          <div className="event-list">
            {events.map((event) => (
              <button
                type="button"
                key={event.id}
                className={`event-row event-button ${selectedEvent?.id === event.id ? "selected" : ""}`}
                onClick={() => onEventChange(event.id)}
              >
                <span className="event-row-copy">
                  <strong>{event.title}</strong>
                  <small>
                    {event.venue} - {new Date(event.date_time).toLocaleString()}
                  </small>
                  <small className="event-description">{event.description || "Event details will be announced soon."}</small>
                  <span className="tier-counts">
                    {["general", "premium", "vip"].map((tier) => {
                      const count = event.tier_counts?.find((item) => item.tier === tier)?.issued_count ?? 0;
                      return <em key={tier}>{tier}: {count}</em>;
                    })}
                  </span>
                </span>
                <span className="seat-total">
                  <b>{event.issued_count}</b>
                  <small>of {event.capacity} issued</small>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="section-heading">
            <p className="eyebrow">Gates for {selectedEvent?.title ?? "Event"}</p>
            <h2>Entry Points</h2>
          </div>
          <div className="gate-list">
            {selectedEventGates.length === 0 && <p className="muted-text">No gates added for this event yet.</p>}
            {selectedEventGates.map((gate) => (
              <article className="gate-row compact" key={gate.gate_id ?? gate.id}>
                <DoorOpen size={20} aria-hidden="true" />
                <div>
                  <h3>{gate.name}</h3>
                  <p>{gate.location}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
