import { Activity, Armchair, TicketCheck } from "lucide-react";

export function OperationsSummary({ ticket, gates, event }) {
  const checkedIn = gates.reduce((sum, gate) => sum + gate.scanned_count, 0);
  const issued = event?.issued_count ?? 0;
  const remaining = event ? Math.max(event.capacity - issued, 0) : 0;

  return (
    <section className="summary-strip" aria-label="Operations summary">
      <div>
        <Armchair size={20} aria-hidden="true" />
        <span>Seats issued</span>
        <strong>{event ? `${issued} / ${event.capacity}` : "No event"}</strong>
      </div>
      <div>
        <Activity size={20} aria-hidden="true" />
        <span>Seats remaining</span>
        <strong>{event ? remaining : "-"}</strong>
      </div>
      <div>
        <TicketCheck size={20} aria-hidden="true" />
        <span>{ticket ? "Your allocated seat" : "Gate check-ins"}</span>
        <strong>{ticket?.seat_number ?? checkedIn}</strong>
      </div>
    </section>
  );
}
