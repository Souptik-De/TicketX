import { Armchair, CalendarDays, MapPin } from "lucide-react";

import { eventDay, eventMonth, eventTiers, formatDate, remainingSeats, TIERS, tierIssuedCount } from "../lib/format";

/**
 * One event as a browsable card, shared by the public homepage and the
 * signed-in "Browse events" view so an attendee sees the same date, venue, and
 * seat availability in both places.
 *
 * Presentational only: the caller owns the button label and what selecting the
 * card means, because on the homepage it leads to a login and once signed in it
 * leads to the registration form. Seat-map events are the one case where the
 * caller also routes somewhere else, because their booking lives on a map.
 *
 * Legacy events keep the General/Premium/VIP preview they have always shown.
 * Seat-map events show the tiers the admin actually defined, with availability
 * taken from the server's tier summaries rather than issued counts -- a held
 * seat is neither sold nor available, so `issued_count` would overstate it.
 */
export function EventCard({ event, actionLabel, actionIcon: ActionIcon, onSelect, isSelected, isComplete }) {
  const isSeatMap = Boolean(event.has_seat_map);
  const tiers = eventTiers(event);
  const remaining = remainingSeats(event);

  const tierSeats = tiers.reduce((sum, tier) => sum + (tier.total ?? 0), 0);
  const tierAvailable = tiers.reduce((sum, tier) => sum + (tier.available ?? 0), 0);
  const occupancy =
    isSeatMap && tierSeats > 0
      ? Math.min(((tierSeats - tierAvailable) / tierSeats) * 100, 100)
      : event.capacity
        ? Math.min((event.issued_count / event.capacity) * 100, 100)
        : 0;
  const soldOut = remaining === 0;
  // A sold-out seat-map event is not a waitlist card in this part: the waitlist
  // will live on the seat page, so the button must stay usable.
  const usesWaitlistStyle = soldOut && !isComplete && !isSeatMap;

  return (
    <article className={`event-card${isSelected ? " event-card-selected" : ""}`}>
      <div className="event-card-topline">
        <time className="event-date" dateTime={event.date_time}>
          <strong>{eventDay(event.date_time)}</strong>
          <span>{eventMonth(event.date_time)}</span>
        </time>
        <span className={`availability-tag${soldOut ? " sold-out" : ""}`}>
          {soldOut ? (isSeatMap ? "Sold out" : "Sold out - waitlist open") : `${remaining} seats left`}
        </span>
      </div>

      <div className="event-card-copy">
        <h3>{event.title}</h3>
        <p>{event.description || "More details about this event will be announced soon."}</p>
        {(event.artist || event.city || event.category) && (
          <div className="event-card-meta">
            {event.artist && <em className="meta-chip">{event.artist}</em>}
            {event.city && <em className="meta-chip">{event.city}</em>}
            {event.category && <em className="meta-chip meta-chip-category">{event.category}</em>}
            {event.is_cancelled && <strong className="meta-chip meta-chip-cancelled">Cancelled</strong>}
          </div>
        )}
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
        <div
          className={`home-tier-list${isSeatMap ? " home-tier-list--seatmap" : ""}`}
          aria-label={isSeatMap ? "Available seats by tier" : "Issued seats by tier"}
        >
          {isSeatMap
            ? tiers.map((tier) => (
                <span key={tier.name}>
                  <strong>{tier.available}</strong>
                  <em>
                    {tier.name} · {tier.total} seats
                  </em>
                </span>
              ))
            : TIERS.map((tier) => (
                <span key={tier}>
                  <strong>{tierIssuedCount(event, tier)}</strong> {tier}
                </span>
              ))}
        </div>
        {ActionIcon && (
          <button
            className={`primary-button${usesWaitlistStyle ? " waitlist-button" : ""}`}
            type="button"
            onClick={() => onSelect(event.id)}
          >
            <ActionIcon size={18} aria-hidden="true" />
            {actionLabel}
          </button>
        )}
      </div>
    </article>
  );
}
