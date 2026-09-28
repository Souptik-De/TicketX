import { Armchair, CalendarDays, MapPin } from "lucide-react";

import { eventDay, eventMonth, formatDate, remainingSeats, TIERS, tierIssuedCount } from "../lib/format";

/**
 * One event as a browsable card, shared by the public homepage and the
 * signed-in "Browse events" view so an attendee sees the same date, venue, and
 * seat availability in both places.
 *
 * Presentational only: the caller owns the button label and what selecting the
 * card means, because on the homepage it leads to a login and once signed in it
 * leads to the registration form.
 */
export function EventCard({ event, actionLabel, actionIcon: ActionIcon, onSelect, isSelected, isComplete }) {
  const remaining = remainingSeats(event);
  const occupancy = event.capacity ? Math.min((event.issued_count / event.capacity) * 100, 100) : 0;
  const soldOut = remaining === 0;

  return (
    <article className={`event-card${isSelected ? " event-card-selected" : ""}`}>
      <div className="event-card-topline">
        <time className="event-date" dateTime={event.date_time}>
          <strong>{eventDay(event.date_time)}</strong>
          <span>{eventMonth(event.date_time)}</span>
        </time>
        <span className={`availability-tag${soldOut ? " sold-out" : ""}`}>
          {soldOut ? "Sold out - waitlist open" : `${remaining} seats left`}
        </span>
      </div>

      <div className="event-card-copy">
        <h3>{event.title}</h3>
        <p>{event.description || "More details about this event will be announced soon."}</p>
      </div>

      <div className="event-facts">
        <span>
          <CalendarDays size={17} aria-hidden="true" /> {formatDate(event.date_time)}
        </span>
        <span>
          <MapPin size={17} aria-hidden="true" /> {event.venue}
        </span>
        <span>
          <Armchair size={17} aria-hidden="true" /> {event.issued_count} of {event.capacity} issued
        </span>
      </div>

      <div className="capacity-meter" aria-label={`${Math.round(occupancy)} percent of seats issued`}>
        <span style={{ width: `${occupancy}%` }} />
      </div>

      <div className="event-card-footer">
        <div className="home-tier-list" aria-label="Issued seats by tier">
          {TIERS.map((tier) => (
            <span key={tier}>
              <strong>{tierIssuedCount(event, tier)}</strong> {tier}
            </span>
          ))}
        </div>
        {ActionIcon && (
          <button className={`primary-button${soldOut && !isComplete ? " waitlist-button" : ""}`} type="button" onClick={() => onSelect(event.id)}>
            <ActionIcon size={18} aria-hidden="true" />
            {actionLabel}
          </button>
        )}
      </div>
    </article>
  );
}
