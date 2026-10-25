import { ArrowLeft, TicketCheck } from "lucide-react";

import { TicketPreview } from "./TicketPreview";

/**
 * The confirmed state: one pass per seat, each with its own QR and download.
 * A booking that another tab already confirmed lands here too, with a note
 * instead of a second set of tickets.
 */
export function BookingSuccess({ event, tickets, alreadyConfirmed = false, onViewTickets, onBookMore }) {
  return (
    <section className="booking-success" aria-labelledby="booking-success-title">
      <div className="panel booking-success-head">
        <p className="eyebrow">Booking confirmed</p>
        <h2 id="booking-success-title">{event?.title ?? "Your tickets"}</h2>
        <p className="muted-text">
          {alreadyConfirmed
            ? "This booking was already confirmed. Here are the passes that belong to it."
            : "Your seats are booked. Each seat has its own pass and QR code."}
        </p>
        <div className="booking-success-actions">
          <button className="primary-button" type="button" onClick={onViewTickets}>
            <TicketCheck size={18} aria-hidden="true" />
            View My Tickets
          </button>
          <button className="ghost-button" type="button" onClick={onBookMore}>
            <ArrowLeft size={18} aria-hidden="true" />
            Book more seats
          </button>
        </div>
      </div>

      {tickets.length === 0 ? (
        <div className="panel">
          <p className="muted-text">
            The tickets are confirmed, but the pass list could not be loaded. Open My Tickets to see them.
          </p>
        </div>
      ) : (
        <div className="booking-success-passes">
          {tickets.map((ticket) => (
            <TicketPreview key={ticket.ticket_id} ticket={ticket} compact />
          ))}
        </div>
      )}
    </section>
  );
}
