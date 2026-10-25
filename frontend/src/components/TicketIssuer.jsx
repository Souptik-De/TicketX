import { useEffect, useState } from "react";
import { Armchair, Clock, Send } from "lucide-react";

import { getTicket, issueTicket } from "../lib/api";
import { remainingSeats } from "../lib/format";
import { SelectedEventSummary } from "./EventBrowser";

/**
 * Step 2 of registering: the details for the event the attendee picked.
 *
 * Given an `event` the form is driven by the browse step, and identity is
 * prefilled from the signed-in account so the common case is a single click.
 * Every field stays editable, because this form has always been able to book
 * someone other than the account holder. With no `event` it falls back to its
 * own dropdown, which is how the other workspaces still use it.
 *
 * Seat-map events never reach the General/Premium/VIP form: their tiers are
 * admin-defined, so the only sensible entry is the seat page.
 */
export function TicketIssuer({ events, initialEventId, event, myTickets, sessionUser, onEventChange, onChooseSeats, onShowTicket, onTicketIssued }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [campusId, setCampusId] = useState("");
  const [tier, setTier] = useState("general");
  const [fallbackEventId, setFallbackEventId] = useState(initialEventId || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [alreadyRegistered, setAlreadyRegistered] = useState(false);
  const [waitlistMessage, setWaitlistMessage] = useState("");

  const isDrivenByBrowse = Boolean(event);
  const legacyEvents = events.filter((item) => !item.has_seat_map);
  const seatMapEvents = events.filter((item) => item.has_seat_map);
  const selectedEvent =
    event ??
    events.find((item) => item.id === Number(fallbackEventId)) ??
    legacyEvents[0] ??
    events[0] ??
    null;
  const seatsLeft = selectedEvent ? remainingSeats(selectedEvent) : 0;

  // Prefill from the account once it is known. A password account carries no
  // email, so that field is left for the attendee rather than guessed at.
  useEffect(() => {
    if (sessionUser) {
      setName((current) => current || sessionUser.display_name || "");
      setEmail((current) => current || sessionUser.email || "");
    }
  }, [sessionUser]);

  useEffect(() => {
    if (initialEventId) {
      setFallbackEventId(initialEventId);
    }
  }, [initialEventId]);

  // A new event starts the form clean: the waitlist notice and the duplicate
  // error both belong to the event that was just left behind.
  useEffect(() => {
    setWaitlistMessage("");
    setError("");
    setAlreadyRegistered(false);
  }, [event?.id, fallbackEventId]);

  // The legacy form's tier list is General/Premium/VIP, which does not exist on
  // a seat-map event. Offering it would issue a ticket in the wrong tier, so
  // the event is handed over to the seat page instead.
  if (selectedEvent?.has_seat_map) {
    return (
      <section className="panel">
        <div className="section-heading">
          <p className="eyebrow">{isDrivenByBrowse ? "Step 2 of 2" : "User Panel"}</p>
          <h2>Choose seats</h2>
        </div>
        <SelectedEventSummary
          event={selectedEvent}
          myTickets={isDrivenByBrowse ? myTickets : []}
          onChangeEvent={onEventChange}
          onShowTicket={onShowTicket}
        />
        <p className="muted-text">
          This event is sold by seat, so there is no General/Premium/VIP choice. The seat map shows exactly what is
          still available.
        </p>
        <button className="primary-button" type="button" onClick={() => onChooseSeats(selectedEvent.id)}>
          <Armchair size={18} aria-hidden="true" />
          Choose seats
        </button>
        {!isDrivenByBrowse && seatMapEvents.length > 1 && (
          <div className="seat-event-choices">
            <p className="muted-text">Other seat-map events:</p>
            {seatMapEvents
              .filter((item) => item.id !== selectedEvent.id)
              .map((item) => (
                <button
                  className="ghost-button"
                  key={item.id}
                  type="button"
                  onClick={() => onChooseSeats(item.id)}
                >
                  <Armchair size={16} aria-hidden="true" />
                  {item.title}
                </button>
              ))}
          </div>
        )}
      </section>
    );
  }

  async function handleSubmit(submitEvent) {
    submitEvent.preventDefault();
    if (!selectedEvent) {
      setError("No event is available yet.");
      return;
    }

    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedCampusId = campusId.trim();

    setError("");
    setWaitlistMessage("");
    setAlreadyRegistered(false);
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
      if (err instanceof Error && err.status === 409) {
        setAlreadyRegistered(true);
        setError("");
      } else {
        setError(err instanceof Error ? err.message : "Could not issue ticket");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  const submitLabel = isSubmitting
    ? seatsLeft > 0
      ? "Issuing..."
      : "Joining waitlist..."
    : seatsLeft > 0
      ? "Register and get ticket"
      : "Join waitlist";

  return (
    <section className="panel">
      <div className="section-heading">
        <p className="eyebrow">{isDrivenByBrowse ? "Step 2 of 2" : "User Panel"}</p>
        <h2>{isDrivenByBrowse ? "Register" : "Get Your Ticket"}</h2>
      </div>
      <form className="ticket-form" onSubmit={handleSubmit}>
        {!isDrivenByBrowse && (
          <label className="full-width">
            Event
            <select value={fallbackEventId || selectedEvent?.id || ""} onChange={(changeEvent) => setFallbackEventId(Number(changeEvent.target.value))} required>
              <option value="" disabled>
                Select an event
              </option>
              {legacyEvents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} - {item.venue}
                </option>
              ))}
            </select>
          </label>
        )}
        {selectedEvent && (
          <SelectedEventSummary
            event={selectedEvent}
            myTickets={isDrivenByBrowse ? myTickets : []}
            onChangeEvent={onEventChange}
            onShowTicket={onShowTicket}
          />
        )}
        <label>
          Attendee name
          <input
            value={name}
            onChange={(changeEvent) => setName(changeEvent.target.value)}
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
            onChange={(changeEvent) => setEmail(changeEvent.target.value)}
            placeholder="Enter email address"
            required
          />
        </label>
        <label>
          Campus ID
          <input
            value={campusId}
            onChange={(changeEvent) => setCampusId(changeEvent.target.value)}
            placeholder="Optional campus ID"
          />
        </label>
        <label>
          Tier
          <select value={tier} onChange={(changeEvent) => setTier(changeEvent.target.value)}>
            <option value="general">General</option>
            <option value="premium">Premium</option>
            <option value="vip">VIP</option>
          </select>
        </label>
        {error && <p className="error-text">{error}</p>}
        {alreadyRegistered && (
          <p className="muted-text full-width" role="status">
            That attendee already holds a ticket for this event, so no second seat was issued. Use the details
            above to open the ticket they already have.
          </p>
        )}
        {waitlistMessage && (
          <div className="waitlist-alert full-width" role="status">
            <Clock size={18} aria-hidden="true" />
            <span>{waitlistMessage}</span>
          </div>
        )}
        <button className="primary-button" disabled={isSubmitting} type="submit">
          <Send size={18} aria-hidden="true" />
          {submitLabel}
        </button>
      </form>

      {!isDrivenByBrowse && seatMapEvents.length > 0 && (
        <div className="seat-event-choices">
          <p className="muted-text">Seat-map events are booked by choosing a seat:</p>
          {seatMapEvents.map((item) => (
            <button className="ghost-button" key={item.id} type="button" onClick={() => onChooseSeats(item.id)}>
              <Armchair size={16} aria-hidden="true" />
              {item.title}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
