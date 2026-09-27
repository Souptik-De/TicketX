import { useState } from "react";
import { BellRing, Clock, TicketCheck, X } from "lucide-react";

/**
 * ET-07: what the attendee sees about their own place in a queue.
 *
 * Two jobs. It shows a live position for anything still waiting, and it is the
 * in-app notice the story asks for when a promotion happens: once an admin
 * revokes a ticket (ET-11), the entry flips to "promoted" and the ticket issued
 * is offered here for collection. The ticket itself is rendered by
 * TicketPreview, which already knows how to draw and share a QR.
 */
export function MyWaitlist({ entries, onShowTicket, onLeave }) {
  const [leavingId, setLeavingId] = useState(null);
  const [error, setError] = useState("");

  const waiting = (entries ?? []).filter((entry) => entry.status === "waiting");
  const promoted = (entries ?? []).filter((entry) => entry.status === "promoted" && entry.promoted_ticket);

  if (waiting.length === 0 && promoted.length === 0) {
    return null;
  }

  async function handleLeave(entryId) {
    setError("");
    setLeavingId(entryId);
    try {
      await onLeave(entryId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not leave the waitlist");
    } finally {
      setLeavingId(null);
    }
  }

  return (
    <section className="panel my-waitlist">
      <div className="section-heading">
        <p className="eyebrow">Your registrations</p>
        <h2>Waitlist</h2>
      </div>

      {promoted.length > 0 && (
        <div className="promotion-alert" role="status">
          <BellRing size={20} aria-hidden="true" />
          <div>
            <strong>A ticket is ready for you.</strong>
            {promoted.map((entry) => (
              <p key={entry.waitlist_entry_id}>
                A seat opened up at <strong>{entry.event_title}</strong> and a ticket was issued to you
                {entry.tier && entry.tier !== "general" ? ` (${entry.tier} tier)` : ""}.
              </p>
            ))}
            {promoted.map((entry) => (
              <button
                className="primary-button"
                key={`show-${entry.waitlist_entry_id}`}
                type="button"
                onClick={() => onShowTicket(entry.promoted_ticket)}
              >
                <TicketCheck size={18} aria-hidden="true" />
                View your ticket
              </button>
            ))}
          </div>
        </div>
      )}

      {waiting.length > 0 && (
        <ul className="waitlist-entries">
          {waiting.map((entry) => (
            <li className="waitlist-entry" key={entry.waitlist_entry_id}>
              <div className="waitlist-entry-copy">
                <p className="waitlist-entry-title">
                  <Clock size={18} aria-hidden="true" />
                  {entry.event_title}
                </p>
                <p className="waitlist-position">
                  You are <strong>#{entry.position}</strong> in line
                </p>
                <p className="muted-text">
                  {entry.attendee_name}
                  {entry.campus_id ? ` - ${entry.campus_id}` : ""}
                  {entry.tier && entry.tier !== "general" ? ` - ${entry.tier} tier requested` : ""}
                </p>
              </div>
              <button
                className="ghost-button"
                type="button"
                disabled={leavingId === entry.waitlist_entry_id}
                onClick={() => handleLeave(entry.waitlist_entry_id)}
              >
                <X size={16} aria-hidden="true" />
                {leavingId === entry.waitlist_entry_id ? "Leaving..." : "Leave waitlist"}
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="muted-text waitlist-queue-note">
        Positions update on their own. If a seat opens up it goes to the person at the front of the line,
        and you will see your ticket here.
      </p>

      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
