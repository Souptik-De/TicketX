import { useEffect, useState } from "react";
import { Armchair, CalendarDays, Send } from "lucide-react";

import { getTicket, issueTicket } from "../lib/api";

export function TicketIssuer({ events, initialEventId, onTicketIssued }) {
  const [name, setName] = useState("Riya Sen");
  const [email, setEmail] = useState("riya@example.edu");
  const [campusId, setCampusId] = useState("CAMP-2026-042");
  const [tier, setTier] = useState("general");
  const [eventId, setEventId] = useState(initialEventId || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const selectedEvent = events.find((event) => event.id === Number(eventId)) ?? events[0];
  const remainingSeats = selectedEvent ? selectedEvent.capacity - selectedEvent.issued_count : 0;

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

    setError("");
    setIsSubmitting(true);
    try {
      const created = await issueTicket({
        event_id: selectedEvent.id,
        attendee_name: name,
        attendee_contact: email,
        campus_id: campusId,
        tier,
      });
      const detail = await getTicket(created.ticket_id);
      onTicketIssued(detail);
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
          <input value={name} onChange={(eventObject) => setName(eventObject.target.value)} required minLength={2} />
        </label>
        <label>
          Email
          <input type="email" value={email} onChange={(eventObject) => setEmail(eventObject.target.value)} required />
        </label>
        <label>
          Campus ID
          <input value={campusId} onChange={(eventObject) => setCampusId(eventObject.target.value)} />
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
        <button className="primary-button" disabled={isSubmitting} type="submit">
          <Send size={18} aria-hidden="true" />
          {isSubmitting ? "Issuing..." : "Issue ticket"}
        </button>
      </form>
    </section>
  );
}
