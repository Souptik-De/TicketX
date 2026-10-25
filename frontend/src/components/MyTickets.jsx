import { CalendarDays, MapPin, Ticket, TicketCheck } from "lucide-react";

import { formatDate } from "../lib/format";
import { TicketPreview } from "./TicketPreview";

/**
 * The caller's ticket history, grouped by event.
 *
 * Seat-map tickets are shown as compact passes so the QR and its PNG download
 * are available without leaving the page. Legacy tickets keep the exact card
 * they had before this screen existed -- only the grouping heading is new --
 * because their "View ticket" button still opens the full pass in the
 * workspace's preview column. A revoked ticket is never presented as valid:
 * it is greyed out, badged, and has no QR or view button.
 */
export function MyTickets({ tickets, onShowTicket, onBrowse, showEmpty = false }) {
  const list = tickets ?? [];

  if (list.length === 0) {
    // The legacy workspace rendered nothing when there were no tickets. Keep
    // that exact behaviour there; only the dedicated My Tickets view owns the
    // empty state.
    if (!showEmpty) return null;
    return (
      <section className="panel my-tickets">
        <div className="section-heading">
          <p className="eyebrow">Your registrations</p>
          <h2>My tickets</h2>
        </div>
        <div className="my-tickets-empty">
          <TicketCheck size={34} aria-hidden="true" />
          <p>You don't have any tickets yet.</p>
          <button className="primary-button" type="button" onClick={onBrowse}>
            Browse events
          </button>
        </div>
      </section>
    );
  }

  const groups = [];
  const byEvent = new Map();

  for (const ticket of list) {
    const eventId = ticket.event?.id;
    let group = byEvent.get(eventId);
    if (!group) {
      group = { event: ticket.event, tickets: [] };
      byEvent.set(eventId, group);
      groups.push(group);
    }
    group.tickets.push(ticket);
  }

  return (
    <section className="panel my-tickets">
      <div className="section-heading">
        <p className="eyebrow">Your registrations</p>
        <h2>My tickets</h2>
      </div>

      <div className="my-ticket-groups">
        {groups.map((group) => (
          <section className="my-ticket-group" key={group.event.id}>
            <div className="my-ticket-group-head">
              <h3>{group.event.title}</h3>
              <p className="my-ticket-facts">
                <span>
                  <CalendarDays size={15} aria-hidden="true" /> {formatDate(group.event.date_time)}
                </span>
                <span>
                  <MapPin size={15} aria-hidden="true" /> {group.event.venue}
                </span>
              </p>
            </div>

            <ul className="my-ticket-list">
              {group.tickets.map((ticket) => (
                <TicketRow key={ticket.ticket_id} ticket={ticket} onShowTicket={onShowTicket} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

function TicketRow({ ticket, onShowTicket }) {
  if (ticket.status === "revoked") {
    return (
      <li className="my-ticket my-ticket--revoked">
        <div className="my-ticket-copy">
          <p className="my-ticket-title">
            <Ticket size={18} aria-hidden="true" />
            {ticket.event.title}
          </p>
          <p className="my-ticket-facts">
            <span>
              <CalendarDays size={15} aria-hidden="true" /> {formatDate(ticket.event.date_time)}
            </span>
            <span>
              <MapPin size={15} aria-hidden="true" /> {ticket.event.venue}
            </span>
          </p>
          <p className="muted-text">
            {ticket.attendee.name}
            {ticket.attendee.campus_id ? ` - ${ticket.attendee.campus_id}` : ""} -{" "}
            <strong>
              {ticket.tier} {ticket.seat_number}
            </strong>
          </p>
          <p className="muted-text">This ticket is no longer valid.</p>
        </div>
        <span className="status-pill revoked">Revoked</span>
      </li>
    );
  }

  if (ticket.event.has_seat_map) {
    return (
      <li className="my-ticket my-ticket--pass">
        <TicketPreview ticket={ticket} compact />
      </li>
    );
  }

  // The legacy card is deliberately unchanged: same fields, same button, same
  // wording. The group heading above it is the only new element.
  const used = ticket.status === "used";

  return (
    <li className="my-ticket">
      <div className="my-ticket-copy">
        <p className="my-ticket-title">
          <Ticket size={18} aria-hidden="true" />
          {ticket.event.title}
        </p>
        <p className="my-ticket-facts">
          <span>
            <CalendarDays size={15} aria-hidden="true" /> {formatDate(ticket.event.date_time)}
          </span>
          <span>
            <MapPin size={15} aria-hidden="true" /> {ticket.event.venue}
          </span>
        </p>
        <p className="muted-text">
          {ticket.attendee.name}
          {ticket.attendee.campus_id ? ` - ${ticket.attendee.campus_id}` : ""} -{" "}
          <strong>
            {ticket.tier} {ticket.seat_number}
          </strong>
        </p>
      </div>
      <button className="ghost-button" type="button" onClick={() => onShowTicket?.(ticket)}>
        <TicketCheck size={16} aria-hidden="true" />
        {used ? "View pass" : "View ticket"}
      </button>
    </li>
  );
}
