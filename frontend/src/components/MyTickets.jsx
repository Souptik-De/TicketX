import { CalendarDays, MapPin, Ticket, TicketCheck } from "lucide-react";

import { formatDate } from "../lib/format";

/**
 * The tickets this account holds, newest first.
 *
 * Before this list, an issued ticket existed only as React state in the parent,
 * so a refresh or a reload lost it and the only way back to a QR pass was to
 * register again. It reads the same `GET /me/registrations` payload the waitlist
 * notice polls, so the two lists can never disagree about a promotion: a ticket
 * handed over by ET-11's revocation appears in both.
 */
export function MyTickets({ tickets, onShowTicket }) {
  if (!tickets || tickets.length === 0) {
    return null;
  }

  return (
    <section className="panel my-tickets">
      <div className="section-heading">
        <p className="eyebrow">Your registrations</p>
        <h2>My tickets</h2>
      </div>

      <ul className="my-ticket-list">
        {tickets.map((ticket) => {
          const used = ticket.status === "used";

          return (
            <li className="my-ticket" key={ticket.ticket_id}>
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
              <button className="ghost-button" type="button" onClick={() => onShowTicket(ticket)}>
                <TicketCheck size={16} aria-hidden="true" />
                {used ? "View pass" : "View ticket"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
