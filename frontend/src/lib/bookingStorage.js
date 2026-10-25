/**
 * Browser-side memory for a booking that is in progress.
 *
 * The hold id is the only way to resume a checkout: the API has no endpoint that
 * lists the caller's active holds. It is scoped by user and event so two people
 * sharing a browser, or one person with two events open, cannot resume the wrong
 * checkout. Waitlist membership is remembered for the same reason -- a refresh
 * should not hide the fact that the attendee already joined the queue.
 *
 * localStorage can throw in private modes, so every access is guarded. A failed
 * write degrades to "cannot resume after a refresh", never to a broken page.
 */

const HOLD_PREFIX = "ticketx.hold.";
const WAITLIST_PREFIX = "ticketx.waitlist.";

function scopedKey(prefix, userId, eventId) {
  return `${prefix}${userId}.${eventId}`;
}

function readNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function readHoldId(userId, eventId) {
  if (!userId || !eventId) return null;
  try {
    return readNumber(localStorage.getItem(scopedKey(HOLD_PREFIX, userId, eventId)));
  } catch {
    return null;
  }
}

export function writeHoldId(userId, eventId, holdId) {
  if (!userId || !eventId || !holdId) return;
  try {
    localStorage.setItem(scopedKey(HOLD_PREFIX, userId, eventId), String(holdId));
  } catch {
    // Resume-after-refresh is a convenience; the active hold still works.
  }
}

export function clearHoldId(userId, eventId) {
  if (!userId || !eventId) return;
  try {
    localStorage.removeItem(scopedKey(HOLD_PREFIX, userId, eventId));
  } catch {
    // Nothing to do: the hold still expires server-side.
  }
}

/** Explicit sign-out only. A 401 keeps the id so the same user can resume. */
export function clearAllHoldIds(userId) {
  if (!userId) return;
  const prefix = `${HOLD_PREFIX}${userId}.`;
  try {
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index);
      if (key?.startsWith(prefix)) {
        localStorage.removeItem(key);
      }
    }
  } catch {
    // Sign-out must still complete even if storage is unavailable.
  }
}

export function readWaitlistState(userId, eventId) {
  if (!userId || !eventId) return null;
  try {
    const raw = localStorage.getItem(scopedKey(WAITLIST_PREFIX, userId, eventId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    return {
      position: Number.isFinite(Number(parsed.position)) ? Number(parsed.position) : null,
      entryId: Number.isFinite(Number(parsed.entry_id)) ? Number(parsed.entry_id) : null,
    };
  } catch {
    return null;
  }
}

export function writeWaitlistState(userId, eventId, { position, entryId }) {
  if (!userId || !eventId) return;
  try {
    localStorage.setItem(
      scopedKey(WAITLIST_PREFIX, userId, eventId),
      JSON.stringify({ position: position ?? null, entry_id: entryId ?? null }),
    );
  } catch {
    // The joined line is a convenience; the server still holds the queue place.
  }
}

export function clearWaitlistState(userId, eventId) {
  if (!userId || !eventId) return;
  try {
    localStorage.removeItem(scopedKey(WAITLIST_PREFIX, userId, eventId));
  } catch {
    // Nothing to do.
  }
}
