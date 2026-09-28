import { Sparkles, Ticket, TicketCheck } from "lucide-react";

import { EventCard } from "./EventCard";
import { remainingSeats } from "../lib/format";

/**
 * "Recommended for you": the same event cards as the catalog, with the reason
 * each one was picked underneath.
 *
 * Deliberately no loading spinner. The endpoint is cheap and always answers with
 * *something* -- when the model has not been consulted yet, or is unreachable,
 * the reason is written by the rules instead. A supplementary row that flickers
 * into place is worse than one that is simply a little more plainly worded, so
 * this renders nothing until the first response arrives and then stays put.
 */
export function SuggestedEvents({ suggestions, myTickets, selectedEventId, onSelect }) {
  const items = (suggestions?.items ?? []).filter((item) => item.event);
  if (items.length === 0) {
    return null;
  }

  const registeredEventIds = new Set((myTickets ?? []).map((ticket) => ticket.event.id));
  const coldStart = suggestions.cold_start;

  return (
    <section className="suggested-events" aria-labelledby="suggested-heading">
      <div className="catalog-heading">
        <div>
          <p className="eyebrow">
            <Sparkles size={14} aria-hidden="true" /> Suggested for you
          </p>
          <h2 id="suggested-heading">You might also like</h2>
        </div>
        <p>
          {coldStart
            ? "Picked from what other attendees are registering for. Register for something and these get more personal."
            : "Based on the events and seating you have registered for."}
        </p>
      </div>

      <div className="event-card-grid">
        {items.map((item) => {
          const isRegistered = registeredEventIds.has(item.event.id);
          const soldOut = remainingSeats(item.event) === 0;

          return (
            <div className="suggestion-wrap" key={item.event.id}>
              <EventCard
                event={item.event}
                isSelected={item.event.id === Number(selectedEventId)}
                isComplete={isRegistered}
                actionIcon={isRegistered ? TicketCheck : Ticket}
                actionLabel={isRegistered ? "You're registered" : soldOut ? "Join waitlist" : "Register"}
                onSelect={onSelect}
              />
              <p className="suggestion-reason">
                <Sparkles size={14} aria-hidden="true" />
                <span>{item.reason}</span>
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
