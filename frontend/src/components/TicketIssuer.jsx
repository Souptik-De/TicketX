import { useEffect, useState } from "react";
import { Armchair, CalendarDays, Clock, Send } from "lucide-react";

import { getTicket, issueTicket } from "../lib/api";

export function TicketIssuer({ events, initialEventId, onTicketIssued }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [campusId, setCampusId] = useState("");
  const [tier, setTier] = useState("general");
  const [eventId, setEventId] = useState(initialEventId || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [waitlistMessage, setWaitlistMessage] = useState("");

  const selectedEvent = events.find((event) => event.id === Number(eventId)) ?? events[0];
  const remainingSeats = selectedEvent ? Math.max(selectedEvent.capacity - selectedEvent.issued_count, 0) : 0;

  useEffect(() => {
    if (initialEventId) {
      setEventId(initialEventId);
    }
  }, [initialEventId]);

  async function handleSubmit(eventObject) {
    eventObject.preventDefault();
    if (!selectedEvent) {
      setError("No event is available yet.");
      return;
    }

    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedCampusId = campusId.trim();

    setError("");
    setWaitlistMessage("");
    if (trimmedName.length < 2) {
      setError("Enter the attendee's full name.");
      return;
    }
    if (!trimmedEmail) {
      setError("Enter a valid email address.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await issueTicket({
        event_id: selectedEvent.id,
        attendee_name: trimmedName,
        attendee_contact: trimmedEmail,
        campus_id: trimmedCampusId || undefined,
        tier,
      });

      if (response.outcome === "waitlisted") {
        const msg = `This event is full. You've been added to the waitlist at position #${response.position}.`;
        setWaitlistMessage(msg);
        onTicketIssued({
          outcome: "waitlisted",
          waitlisted: true,
          position: response.position,
          event_id: response.event_id,
          event: selectedEvent,
          attendee: { name: trimmedName, contact_email: trimmedEmail, campus_id: trimmedCampusId },
        });
      } else {
        setWaitlistMessage("");
        const detail = await getTicket(response.ticket_id);
        onTicketIssued(detail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not issue ticket");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <p className="eyebrow">User Panel</p>
        <h2>Get Your Ticket</h2>
      </div>
      <form className="ticket-form" onSubmit={handleSubmit}>
        <label className="full-width">
          Event
          <select value={eventId || selectedEvent?.id || ""} onChange={(eventObject) => setEventId(Number(eventObject.target.value))} required>
            <option value="" disabled>
              Select an event
            </option>
            {events.map((event) => (
              <option key={event.id} value={event.id}>
                {event.title} - {event.venue}
              </option>
            ))}
          </select>
        </label>
        {selectedEvent && (
          <article className="event-brief full-width">
            <div>
              <CalendarDays size={20} aria-hidden="true" />
              <span>
                <strong>{selectedEvent.title}</strong>
                <small>{new Date(selectedEvent.date_time).toLocaleString()} at {selectedEvent.venue}</small>
              </span>
            </div>
            <p>{selectedEvent.description || "Event details will be announced soon."}</p>
            <div className="availability-line">
              <span><Armchair size={18} aria-hidden="true" /> {selectedEvent.issued_count} issued</span>
              <strong>{remainingSeats} remaining</strong>
            </div>
            {selectedEvent.revoked_count > 0 && (
              <p className="muted-text">{selectedEvent.revoked_count} revoked, seats released</p>
            )}
            <div className="tier-counts">
              {["general", "premium", "vip"].map((tierName) => {
                const count = selectedEvent.tier_counts?.find((item) => item.tier === tierName)?.issued_count ?? 0;
                return <em key={tierName}>{tierName}: {count}</em>;
              })}
            </div>
          </article>
        )}
        <label>
          Attendee name
          <input
            value={name}
            onChange={(eventObject) => setName(eventObject.target.value)}
            placeholder="Enter attendee full name"
            required
            minLength={2}
          />
        </label>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(eventObject) => setEmail(eventObject.target.value)}
            placeholder="Enter email address"
            required
          />
        </label>
        <label>
          Campus ID
          <input
            value={campusId}
            onChange={(eventObject) => setCampusId(eventObject.target.value)}
            placeholder="Optional campus ID"
          />
        </label>
        <label>
          Tier
          <select value={tier} onChange={(eventObject) => setTier(eventObject.target.value)}>
            <option value="general">General</option>
            <option value="premium">Premium</option>
            <option value="vip">VIP</option>
          </select>
        </label>
        {error && <p className="error-text">{error}</p>}
        {waitlistMessage && (
          <div className="waitlist-alert full-width" role="status">
            <Clock size={18} aria-hidden="true" />
            <span>{waitlistMessage}</span>
          </div>
        )}
        <button className="primary-button" disabled={isSubmitting} type="submit">
          <Send size={18} aria-hidden="true" />
          {isSubmitting
            ? remainingSeats > 0
              ? "Issuing..."
              : "Joining waitlist..."
            : remainingSeats > 0
              ? "Issue ticket"
              : "Join waitlist"}
        </button>
      </form>
    </section>
  );
}
