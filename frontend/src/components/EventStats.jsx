import { useEffect, useMemo, useState } from "react";
import { BarChart3, DoorOpen, Search, TicketCheck, UsersRound } from "lucide-react";

import { getEventStats } from "../lib/api";

export function EventStats({ eventId }) {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    if (!eventId) {
      setStats(null);
      return;
    }

    let isActive = true;
    setIsLoading(true);
    setError("");
    getEventStats(eventId)
      .then((data) => {
        if (isActive) {
          setStats(data);
        }
      })
      .catch((err) => {
        if (isActive) {
          setError(err instanceof Error ? err.message : "Could not load event stats");
          setStats(null);
        }
      })
      .finally(() => {
        if (isActive) {
          setIsLoading(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [eventId]);

  const filteredTickets = useMemo(() => {
    if (!stats) return [];
    const term = search.trim().toLowerCase();
    return stats.tickets.filter((ticket) => {
      if (filter === "checked" && !ticket.checked_in) return false;
      if (filter === "pending" && ticket.checked_in) return false;
      if (!term) return true;
      return [ticket.attendee_name, ticket.campus_id, ticket.contact_email, ticket.seat_number, ticket.tier]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(term));
    });
  }, [stats, search, filter]);

  if (!eventId) {
    return null;
  }

  return (
    <div className="panel">
      <div className="section-heading row-heading">
        <div>
          <p className="eyebrow">Stats for {stats?.title ?? "Event"}</p>
          <h2>EventDetails</h2>
        </div>
        {stats && (
          <span className="sync-state">
            {stats.checked_in} of {stats.issued} checked in ({stats.check_in_rate}%)
          </span>
        )}
      </div>

      {isLoading && <p className="muted-text">Loading ticket stats...</p>}
      {error && <p className="error-text">{error}</p>}

      {stats && !isLoading && (
        <>
          <div className="admin-metrics">
            <div>
              <UsersRound size={20} aria-hidden="true" />
              <span>Tickets issued</span>
              <strong>
                {stats.issued} / {stats.capacity}
              </strong>
            </div>
            <div>
              <TicketCheck size={20} aria-hidden="true" />
              <span>Checked in</span>
              <strong>{stats.checked_in}</strong>
            </div>
            <div>
              <BarChart3 size={20} aria-hidden="true" />
              <span>Still outside</span>
              <strong>{stats.issued - stats.checked_in}</strong>
            </div>
          </div>

          <div className="stats-section">
            <h3>Check-ins by gate</h3>
            {stats.gate_breakdown.length === 0 ? (
              <p className="muted-text">No gates added for this event yet.</p>
            ) : (
              <div className="gate-list">
                {stats.gate_breakdown.map((gate) => (
                  <article className="gate-row compact" key={gate.gate_id}>
                    <DoorOpen size={20} aria-hidden="true" />
                    <div>
                      <h3>{gate.name}</h3>
                      <p>{gate.location}</p>
                    </div>
                    <div className="gate-number">
                      <strong>{gate.scanned_count}</strong>
                      <span>checked in</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>

          <div className="stats-section">
            <h3>Tickets by tier</h3>
            {stats.tier_breakdown.length === 0 ? (
              <p className="muted-text">No tickets issued yet.</p>
            ) : (
              <div className="stats-table-wrap">
                <table className="stats-table">
                  <thead>
                    <tr>
                      <th>Tier</th>
                      <th>Issued</th>
                      <th>Checked in</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.tier_breakdown.map((row) => (
                      <tr key={row.tier}>
                        <td className="capitalize">{row.tier}</td>
                        <td>{row.issued}</td>
                        <td>{row.checked_in}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="stats-section">
            <div className="row-heading">
              <h3>Who got a ticket and where it was checked</h3>
              <span className="sync-state">{filteredTickets.length} shown</span>
            </div>
            <div className="stats-toolbar">
              <label className="stats-search">
                <Search size={16} aria-hidden="true" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search name, email, seat..."
                />
              </label>
              <div className="stats-filters">
                {[
                  { id: "all", label: "All" },
                  { id: "checked", label: "Checked in" },
                  { id: "pending", label: "Not yet in" },
                ].map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className={`ghost-button stats-filter ${filter === option.id ? "active" : ""}`}
                    onClick={() => setFilter(option.id)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            {filteredTickets.length === 0 ? (
              <p className="muted-text">No tickets match this view yet.</p>
            ) : (
              <div className="stats-table-wrap">
                <table className="stats-table">
                  <thead>
                    <tr>
                      <th>Attendee</th>
                      <th>Ticket</th>
                      <th>Status</th>
                      <th>Checked at</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTickets.map((ticket) => (
                      <tr key={ticket.ticket_id}>
                        <td>
                          <strong>{ticket.attendee_name}</strong>
                          <small className="stats-sub">
                            {ticket.contact_email}
                            {ticket.campus_id && ticket.campus_id !== ticket.contact_email ? ` - ${ticket.campus_id}` : ""}
                          </small>
                        </td>
                        <td>
                          <span className="capitalize">
                            {ticket.tier} - {ticket.seat_number}
                          </span>
                          <small className="stats-sub">#{ticket.ticket_id}</small>
                        </td>
                        <td>
                          <span className={`status-pill ${ticket.checked_in ? "in" : "out"}`}>
                            {ticket.checked_in ? "In" : "Outside"}
                          </span>
                          <small className="stats-sub">{ticket.status}</small>
                        </td>
                        <td>
                          {ticket.checked_in ? (
                            <>
                              <strong>{ticket.check_gate_name}</strong>
                              <small className="stats-sub">
                                {ticket.check_gate_location}
                                {ticket.checked_at ? ` - ${new Date(ticket.checked_at).toLocaleString()}` : ""}
                                {ticket.checked_by ? ` by ${ticket.checked_by}` : ""}
                              </small>
                            </>
                          ) : (
                            <span className="muted-text">Not scanned yet</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
