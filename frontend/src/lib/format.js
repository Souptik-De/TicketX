const dateTimeFormat = new Intl.DateTimeFormat("en-IN", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const monthFormat = new Intl.DateTimeFormat("en-IN", { month: "short" });
const dayFormat = new Intl.DateTimeFormat("en-IN", { day: "2-digit" });

export function formatDate(value) {
  return dateTimeFormat.format(new Date(value));
}

export function eventMonth(value) {
  return monthFormat.format(new Date(value));
}

export function eventDay(value) {
  return dayFormat.format(new Date(value));
}

export const TIERS = ["general", "premium", "vip"];

export function tierIssuedCount(event, tier) {
  return event.tier_counts?.find((item) => item.tier === tier)?.issued_count ?? 0;
}

/** The tier summaries the API returns for a seat-map event, and [] for legacy. */
export function eventTiers(event) {
  return Array.isArray(event?.tiers) ? event.tiers : [];
}

/** Seats on sale for a seat-map event: blocked seats are not part of capacity. */
export function seatMapCapacity(event) {
  return eventTiers(event).reduce((sum, tier) => sum + (tier.total ?? 0), 0);
}

/**
 * Seats still available to book.
 *
 * A seat-map event cannot use capacity - issued_count: a held seat has no ticket
 * yet but is not available either, and that sum would overstate what is left.
 * The tier summaries already carry the server's answer, so they are used as-is.
 */
export function remainingSeats(event) {
  if (event?.has_seat_map) {
    return Math.max(
      eventTiers(event).reduce((sum, tier) => sum + (tier.available ?? 0), 0),
      0,
    );
  }
  return Math.max((event?.capacity ?? 0) - (event?.issued_count ?? 0), 0);
}
