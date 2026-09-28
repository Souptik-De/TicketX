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

export function remainingSeats(event) {
  return Math.max(event.capacity - event.issued_count, 0);
}
