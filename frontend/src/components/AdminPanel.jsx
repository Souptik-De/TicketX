import { useEffect, useState } from "react";
import { Armchair, Ban, CalendarPlus, DoorOpen, MapPin, Pencil, Plus, RotateCcw, UsersRound, Trash2 } from "lucide-react";

import { createEvent, createGate, deleteEvent, getEventFacets, revokeTicket, updateEvent } from "../lib/api";
import { emptyLayout, serializeLayout, validateLayout } from "../lib/layoutToSeats";
import { EventStats } from "./EventStats";
import { SeatLayoutBuilder } from "./SeatLayoutBuilder";

export function AdminPanel({ events, gates, selectedEventId, onEventChange, onEventCreated, onEventUpdated, onGateCreated, onEventDeleted }) {
  const [title, setTitle] = useState("Freshers Night 2026");
  const [description, setDescription] = useState(
    "An evening of live performances, student showcases, food stalls, and campus celebrations.",
  );
  const [venue, setVenue] = useState("Seminar Hall");
  const [dateTime, setDateTime] = useState("2026-09-18T18:30");
  const [capacity, setCapacity] = useState(250);
  // The whole seat layout, tiers and rows, lives in this one object: a rejected
  // create must not cost the admin their work.
  const [eventMode, setEventMode] = useState("simple");
  const [seatLayout, setSeatLayout] = useState(emptyLayout);
  const [gateName, setGateName] = useState("VIP Gate");
  const [gateLocation, setGateLocation] = useState("Auditorium east entrance");
  const [volunteerName, setVolunteerName] = useState("Gate Volunteer");
  const [artist, setArtist] = useState("");
  const [city, setCity] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [eventMessage, setEventMessage] = useState("");
  const [eventError, setEventError] = useState("");
  const [isSavingEvent, setIsSavingEvent] = useState(false);
  const [isSavingGate, setIsSavingGate] = useState(false);
  const [isDeletingEvent, setIsDeletingEvent] = useState(false);
  // Edit-metadata form, prefilled from the selected event. The fields are
  // optional on the wire: an empty artist is sent as null to clear it.
  const [editTitle, setEditTitle] = useState("");
  const [editArtist, setEditArtist] = useState("");
  const [editCity, setEditCity] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [isSavingUpdate, setIsSavingUpdate] = useState(false);
  const [isTogglingCancel, setIsTogglingCancel] = useState(false);
  const [updateMessage, setUpdateMessage] = useState("");
  const [updateError, setUpdateError] = useState("");

  useEffect(() => {
    setUpdateMessage("");
    setUpdateError("");
    if (!selectedEvent) {
      setEditTitle("");
      setEditArtist("");
      setEditCity("");
      setEditCategory("");
      return;
    }
    setEditTitle(selectedEvent.title);
    setEditArtist(selectedEvent.artist ?? "");
    setEditCity(selectedEvent.city ?? "");
    setEditCategory(selectedEvent.category ?? "");
  }, [selectedEvent?.id]);

  // Category suggestions come from the catalog facets so admins reuse the same
  // values attendees can filter by, while free text is still accepted.
  useEffect(() => {
    let cancelled = false;
    getEventFacets()
      .then((data) => {
        if (!cancelled) setCategories(data.categories ?? []);
      })
      .catch(() => {
        // Facets are a nicety: fall back to a plain free-text field.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const layoutValidation = validateLayout(seatLayout);
  const seatMapInvalid = eventMode === "map" && !layoutValidation.valid;

  const selectedEvent = events.find((event) => event.id === Number(selectedEventId)) ?? events[0];
  const selectedEventGates = selectedEvent ? gates.filter((gate) => gate.event_id === selectedEvent.id) : [];
  const totalCapacity = events.reduce((sum, event) => sum + event.capacity, 0);
  const totalIssued = events.reduce((sum, event) => sum + event.issued_count, 0);
  const totalCheckedIn = gates.reduce((sum, gate) => sum + (gate.scanned_count || 0), 0);

  async function handleEventSubmit(eventObject) {
    eventObject.preventDefault();
    setEventError("");
    setEventMessage("");

    if (eventMode === "map" && !layoutValidation.valid) {
      setEventError("Fix the seat map errors before creating the event.");
      return;
    }

    setIsSavingEvent(true);

    try {
      const payload = {
        title,
        description,
        venue,
        date_time: new Date(dateTime).toISOString(),
      };
      if (artist.trim()) payload.artist = artist.trim();
      if (city.trim()) payload.city = city.trim();
      if (category.trim()) payload.category = category.trim();
      if (eventMode === "map") {
        // The server derives capacity from the sellable seats, so sending one
        // would just be a second, conflicting source of truth.
        payload.seat_layout = serializeLayout(seatLayout);
      } else {
        payload.capacity = capacity;
      }

      const created = await createEvent(payload);
      onEventCreated(created);
      setEventMessage(
        created.has_seat_map
          ? `${created.title} is ready with ${created.capacity} seats across ${created.tiers?.length ?? 0} tiers.`
          : `${created.title} is ready for tickets and gates.`,
      );
    } catch (err) {
      // A 422's messages are already readable (api.js joins the field paths),
      // and the layout object is untouched, so the admin can fix and retry.
      setEventError(err instanceof Error ? err.message : "Could not create event");
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

  async function handleDeleteEvent(eventId, eventObject) {
    eventObject.stopPropagation();
    if (!window.confirm("Are you sure you want to delete this event? This action cannot be undone.")) return;

    setError("");
    setMessage("");
    setIsDeletingEvent(true);

    try {
      await deleteEvent(eventId);
      onEventDeleted(eventId);
      setMessage("Event deleted successfully.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete event");
    } finally {
      setIsDeletingEvent(false);
    }
  }

  async function handleEventUpdate(submitEvent) {
    submitEvent.preventDefault();
    if (!selectedEvent) {
      setUpdateError("Select an event to edit first.");
      return;
    }

    setUpdateError("");
    setUpdateMessage("");
    setIsSavingUpdate(true);

    try {
      const updated = await updateEvent(selectedEvent.id, {
        title: editTitle.trim(),
        artist: editArtist.trim() || null,
        city: editCity.trim() || null,
        category: editCategory.trim() || null,
      });
      onEventUpdated(updated);
      setUpdateMessage(`Details for ${updated.title} saved.`);
    } catch (err) {
      setUpdateError(err instanceof Error ? err.message : "Could not update event");
    } finally {
      setIsSavingUpdate(false);
    }
  }

  async function handleToggleCancel() {
    if (!selectedEvent) {
      setUpdateError("Select an event to edit first.");
      return;
    }

    setUpdateError("");
    setUpdateMessage("");
    setIsTogglingCancel(true);

    try {
      const updated = await updateEvent(selectedEvent.id, { is_cancelled: !selectedEvent.is_cancelled });
      onEventUpdated(updated);
      setUpdateMessage(
        updated.is_cancelled
          ? `${updated.title} is cancelled. It is hidden from search and the catalog.`
          : `${updated.title} is live again.`,
      );
    } catch (err) {
      setUpdateError(err instanceof Error ? err.message : "Could not change the event status");
    } finally {
      setIsTogglingCancel(false);
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
              Artist
              <input value={artist} onChange={(event) => setArtist(event.target.value)} maxLength={160} placeholder="Optional" />
            </label>
            <label>
              City
              <input value={city} onChange={(event) => setCity(event.target.value)} maxLength={80} placeholder="Optional" />
            </label>
            <label>
              Category
              <input
                list="event-category"
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                maxLength={80}
                placeholder="Optional"
              />
              <datalist id="event-category">
                {categories.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </label>
            {eventMode === "simple" && (
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
            )}
            <label className="full-width">
              Date and time
              <input type="datetime-local" value={dateTime} onChange={(event) => setDateTime(event.target.value)} required />
            </label>

            <div className="full-width">
              <SeatLayoutBuilder
                mode={eventMode}
                onModeChange={setEventMode}
                layout={seatLayout}
                onLayoutChange={setSeatLayout}
                validation={layoutValidation}
                disabled={isSavingEvent}
              />
            </div>

            {eventError && (
              <p className="error-text" role="alert">
                {eventError}
              </p>
            )}
            {eventMessage && <p className="success-text">{eventMessage}</p>}
            <button className="primary-button" disabled={isSavingEvent || seatMapInvalid} type="submit">
              <CalendarPlus size={18} aria-hidden="true" />
              {isSavingEvent ? "Creating..." : "Create event"}
            </button>
          </form>
        </div>

        <div className="panel admin-form-panel">
          <div className="section-heading">
            <p className="eyebrow">Catalog Search Keys</p>
            <h2>{selectedEvent ? `Edit ${selectedEvent.title}` : "Edit Event"}</h2>
          </div>
          {!selectedEvent ? (
            <p className="muted-text">Create an event first, then its search details can be edited here.</p>
          ) : (
            <form className="ticket-form" onSubmit={handleEventUpdate}>
              <label className="full-width">
                Title
                <input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} required minLength={3} maxLength={160} />
              </label>
              <label>
                Artist
                <input value={editArtist} onChange={(event) => setEditArtist(event.target.value)} maxLength={160} placeholder="Searchable — leave empty to clear" />
              </label>
              <label>
                City
                <input value={editCity} onChange={(event) => setEditCity(event.target.value)} maxLength={80} placeholder="Searchable — leave empty to clear" />
              </label>
              <label>
                Category
                <input
                  list="edit-event-category"
                  value={editCategory}
                  onChange={(event) => setEditCategory(event.target.value)}
                  maxLength={80}
                  placeholder="Searchable — leave empty to clear"
                />
                <datalist id="edit-event-category">
                  {categories.map((name) => (
                    <option key={name} value={name} />
                  ))}
                </datalist>
              </label>

              {updateError && (
                <p className="error-text" role="alert">
                  {updateError}
                </p>
              )}
              {updateMessage && <p className="success-text">{updateMessage}</p>}
              <div className="edit-event-actions full-width">
                <button className="primary-button" disabled={isSavingUpdate || !editTitle.trim()} type="submit">
                  <Pencil size={18} aria-hidden="true" />
                  {isSavingUpdate ? "Saving..." : "Save details"}
                </button>
                <button
                  className="ghost-button"
                  disabled={isTogglingCancel}
                  type="button"
                  onClick={handleToggleCancel}
                >
                  {selectedEvent.is_cancelled ? (
                    <>
                      <RotateCcw size={18} aria-hidden="true" />
                      Restore event
                    </>
                  ) : (
                    <>
                      <Ban size={18} aria-hidden="true" />
                      Cancel event
                    </>
                  )}
                </button>
              </div>
              {selectedEvent.is_cancelled && (
                <p className="muted-text full-width">
                  This event is cancelled: it is hidden from public search and the catalog.
                </p>
              )}
            </form>
          )}
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
                  <strong>
                    {event.title}
                    {event.is_cancelled && <em className="meta-chip meta-chip-cancelled">cancelled</em>}
                  </strong>
                  <small>
                    {event.venue} - {new Date(event.date_time).toLocaleString()}
                  </small>
                  <small className="event-description">{event.description || "Event details will be announced soon."}</small>
                  <span className="tier-counts">
                    {event.has_seat_map ? (
                      <em className="seatmap-chip">Seat map - {event.capacity} seats</em>
                    ) : (
                      ["general", "premium", "vip"].map((tier) => {
                        const count = event.tier_counts?.find((item) => item.tier === tier)?.issued_count ?? 0;
                        return <em key={tier}>{tier}: {count}</em>;
                      })
                    )}
                  </span>
                </span>
                <span className="seat-total" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.5rem' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <b>{event.issued_count}</b>
                    <small>of {event.capacity} issued</small>
                  </div>
                  <button 
                    type="button" 
                    onClick={(e) => handleDeleteEvent(event.id, e)} 
                    disabled={isDeletingEvent}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'red', marginTop: '0.5rem' }}
                    title="Delete Event"
                  >
                    <Trash2 size={18} aria-hidden="true" />
                  </button>
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

        <EventStats
          eventId={selectedEvent?.id}
          hasSeatMap={selectedEvent?.has_seat_map ?? false}
          onRevokeTicket={revokeTicket}
        />
      </div>
    </section>
  );
}
