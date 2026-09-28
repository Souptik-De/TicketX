import { ArrowLeft, CalendarDays, Ticket, TicketCheck } from "lucide-react";

import { EventCard } from "./EventCard";
import { formatDate, remainingSeats, TIERS, tierIssuedCount } from "../lib/format";

/**
 * The browse step that a signed-in attendee lands on: pick an event, then
 * register for it.
 *
 * This is the same catalog the public homepage shows, plus one piece of
 * knowledge the public page cannot have -- which events this account already
 * holds a live ticket for. That is what turns the button into a settled
 * "You're registered" instead of a second seat request the API would reject,
 * and it is why the flow is worth having behind a login at all.
 */
export function EventBrowser({ events, isLoading, error, myTickets, selectedEventId, onSelect }) {
  const registeredEventIds = new Set((myTickets ?? []).map((ticket) => ticket.event.id));

  return (
    <section className="event-catalog" id="events">
      <div className="catalog-heading">
        <div>
          <p className="eyebrow">Step 1 of 2</p>
          <h2>Browse events</h2>
        </div>
        <p>Choose an event to register. Each one shows its date, venue, and how many seats are left.</p>
      </div>

      {error && <div className="banner-error">{error}</div>}
      {isLoading && <div className="catalog-empty">Loading upcoming events...</div>}
      {!isLoading && !error && events.length === 0 && (
        <div className="catalog-empty">No events are accepting tickets right now.</div>
      )}

      <div className="event-card-grid">
        {events.map((event) => {
          const isRegistered = registeredEventIds.has(event.id);
          const soldOut = remainingSeats(event) === 0;

          return (
            <EventCard
              key={event.id}
              event={event}
              isSelected={event.id === Number(selectedEventId)}
              isComplete={isRegistered}
              actionIcon={isRegistered ? TicketCheck : Ticket}
              actionLabel={isRegistered ? "You're registered" : soldOut ? "Join waitlist" : "Register"}
              onSelect={onSelect}
            />
          );
        })}
      </div>
    </section>
  );
}

/**
 * The chosen event, pinned above the form, so the attendee can see what they are
 * registering for and jump back to the catalog without losing their place.
 *
 * When the account already holds a ticket for this event it is listed here
 * rather than blocking the form. One account can hold tickets for several
 * attendees at the same event -- the form has always allowed that -- so the
 * right thing to do is put the existing pass in reach and let the attendee
 * decide, rather than disabling registration outright.
 */
export function SelectedEventSummary({ event, myTickets, onChangeEvent, onShowTicket }) {
  if (!event) {
    return null;
  }

  const remaining = remainingSeats(event);
  const soldOut = remaining === 0;
  const heldTickets = (myTickets ?? []).filter((ticket) => ticket.event.id === event.id);

  return (
    <article className="event-brief full-width selected-event-summary">
      <div>
        <CalendarDays size={20} aria-hidden="true" />
        <span>
          <strong>{event.title}</strong>
          <small>
            {formatDate(event.date_time)} at {event.venue}
          </small>
        </span>
      </div>
      <p>{event.description || "Event details will be announced soon."}</p>
      <div className="availability-line">
        <span>{event.issued_count} issued</span>
        <strong>{remaining} remaining</strong>
      </div>
      {event.revoked_count > 0 && <p className="muted-text">{event.revoked_count} revoked, seats released</p>}
      <div className="tier-counts">
        {TIERS.map((tier) => (
          <em key={tier}>
            {tier}: {tierIssuedCount(event, tier)}
          </em>
        ))}
      </div>

      {heldTickets.length > 0 && (
        <div className="held-tickets">
          <p className="muted-text">
            {heldTickets.length === 1 ? "You already hold a ticket here." : `You already hold ${heldTickets.length} tickets here.`}
          </p>
          {heldTickets.map((ticket) => (
            <button className="ghost-button" key={ticket.ticket_id} type="button" onClick={() => onShowTicket(ticket)}>
              <TicketCheck size={16} aria-hidden="true" />
              {ticket.attendee.name} - {ticket.tier} {ticket.seat_number}
            </button>
          ))}
        </div>
      )}

      <div className="selected-event-actions">
        <button className="ghost-button" type="button" onClick={onChangeEvent}>
          <ArrowLeft size={16} aria-hidden="true" />
          Choose a different event
        </button>
        <span className="muted-text">
          {heldTickets.length > 0
            ? "Registering again here is for a different attendee."
            : soldOut
              ? "Joining the waitlist puts you in the queue"
              : "A seat is reserved the moment you register"}
        </span>
      </div>
    </article>
  );
}
