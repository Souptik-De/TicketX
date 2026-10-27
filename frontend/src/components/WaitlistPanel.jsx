import { useEffect, useState } from "react";
import { BellRing, Clock, TicketCheck } from "lucide-react";

import { getMyRegistrations, joinEventWaitlist } from "../lib/api";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * The full-house state of a seat page.
 *
 * The queue is only offered when the server says zero seats are available
 * event-wide. Held seats count as unavailable, so this matches what the map
 * shows. Promotion is not pushed anywhere: the attendee sees the ticket appear
 * in My Tickets on the next poll, which is the existing in-app notice.
 */
export function WaitlistPanel({
  event,
  sessionUser,
  joined,
  onJoined,
  onSeatsAvailable,
  onUnauthorized,
  onViewTickets,
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [campusId, setCampusId] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (sessionUser) {
      setName((current) => current || sessionUser.display_name || "");
      setEmail((current) => current || sessionUser.email || "");
    }
  }, [sessionUser]);

  async function handleJoin(submitEvent) {
    submitEvent.preventDefault();
    if (isSubmitting) return;

    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedCampusId = campusId.trim();

    setError("");
    if (trimmedName.length < 2) {
      setError("Enter the attendee's full name.");
      return;
    }
    if (!trimmedEmail || !EMAIL_PATTERN.test(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await joinEventWaitlist(event.id, {
        attendee_name: trimmedName,
        attendee_contact: trimmedEmail,
        campus_id: trimmedCampusId || undefined,
      });
      onJoined?.({ position: response.position, entryId: response.waitlist_entry_id });
    } catch (joinError) {
      if (joinError?.status === 401) {
        onUnauthorized?.();
        return;
      }

      const message = String(joinError?.message || "").toLowerCase();
      if (joinError?.status === 409 && message.includes("seats are still available")) {
        onSeatsAvailable?.();
        return;
      }
      if (joinError?.status === 409 && message.includes("already on the waitlist")) {
        // There is no per-event waitlist endpoint, so the registrations payload
        // is the only way to recover the position the server already knows.
        try {
          const data = await getMyRegistrations();
          const entry = (data?.waitlist ?? []).find(
            (item) => item.event_id === event.id && item.status === "waiting",
          );
          if (entry) {
            onJoined?.({ position: entry.position, entryId: entry.waitlist_entry_id });
            return;
          }
        } catch (lookupError) {
          if (lookupError?.status === 401) {
            onUnauthorized?.();
            return;
          }
        }
        setError("You're already on the waitlist for this event.");
        return;
      }

      setError(joinError?.message || "Could not join the waitlist. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="waitlist-banner">
      <div className="waitlist-banner-head">
        <BellRing size={22} aria-hidden="true" />
        <div>
          <h3>No seats available right now</h3>
          <p>
            If an organizer cancels a ticket, the first person on the waitlist gets that seat automatically. Seats
            from unfinished checkouts reappear on this map for everyone.
          </p>
        </div>
      </div>

      {joined ? (
        <div className="waitlist-joined" role="status">
          {joined.status === "promoted" ? (
            <>
              <p className="success-text">A seat opened up and your ticket is ready.</p>
              <p className="muted-text">Open My Tickets to see the pass and its QR code.</p>
              {onViewTickets && (
                <button className="primary-button" type="button" onClick={onViewTickets}>
                  <TicketCheck size={18} aria-hidden="true" />
                  View My Tickets
                </button>
              )}
            </>
          ) : (
            <>
              <p className="success-text">
                {joined.position ? `You're number ${joined.position} on the waitlist` : "You're on the waitlist"}
              </p>
              <p className="muted-text">If a seat opens up, the ticket appears in My Tickets.</p>
            </>
          )}
        </div>
      ) : (
        <form className="ticket-form waitlist-form" onSubmit={handleJoin}>
          <label>
            Attendee name
            <input
              value={name}
              onChange={(changeEvent) => setName(changeEvent.target.value)}
              placeholder="Enter attendee full name"
              required
              minLength={2}
              autoComplete="name"
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
              autoComplete="email"
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
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          <button className="primary-button" type="submit" disabled={isSubmitting}>
            <Clock size={18} aria-hidden="true" />
            {isSubmitting ? "Joining..." : "Join the waitlist"}
          </button>
        </form>
      )}
    </div>
  );
}
