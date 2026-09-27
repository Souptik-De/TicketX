import { useEffect, useMemo, useState } from "react";
import { BarChart3, DoorOpen, Search, TicketCheck, UsersRound } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getEventStats, revokeTicket } from "../lib/api";

export function EventStats({ eventId, onRevokeTicket }) {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [revokingId, setRevokingId] = useState(null);
  const [revokeError, setRevokeError] = useState("");
  const [promotedInfo, setPromotedInfo] = useState({});

  async function handleRevoke(ticketId) {
    setRevokeError("");
    setRevokingId(ticketId);
    try {
      const res = onRevokeTicket ? await onRevokeTicket(ticketId) : await revokeTicket(ticketId);
      if (res?.promoted_attendee) {
        setPromotedInfo((prev) => ({
          ...prev,
          [ticketId]: res.promoted_attendee,
        }));
      }
      try {
        const freshStats = await getEventStats(eventId);
        setStats(freshStats);
      } catch {
        setStats((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            tickets: prev.tickets.map((t) =>
              t.ticket_id === ticketId ? { ...t, status: "revoked" } : t
            ),
          };
        });
      }
    } catch (err) {
      setRevokeError(err instanceof Error ? err.message : "Could not revoke ticket");
    } finally {
      setRevokingId(null);
    }
  }

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

  const tierChartData = useMemo(() => {
    if (!stats) return [];
    return stats.tier_breakdown.map((row) => ({
      tier: row.tier.charAt(0).toUpperCase() + row.tier.slice(1),
      Issued: row.issued,
      Checked: row.checked_in,
    }));
  }, [stats]);

  const gateChartData = useMemo(() => {
    if (!stats) return [];
    return stats.gate_breakdown.map((gate) => ({
      name: gate.name,
      Checked: gate.scanned_count,
    }));
  }, [stats]);

  const statusPieData = useMemo(() => {
    if (!stats) return [];
    return [
      { name: "Checked in", value: stats.checked_in },
      { name: "Still outside", value: Math.max(stats.issued - stats.checked_in, 0) },
    ];
  }, [stats]);

  const tooltipStyle = {
    backgroundColor: "var(--surface-solid)",
    border: "1px solid var(--line)",
    borderRadius: "8px",
    color: "var(--text)",
    fontSize: "0.85rem",
  };

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
            <h3>Charts</h3>
            {stats.issued === 0 ? (
              <p className="muted-text">Charts will appear once tickets are issued.</p>
            ) : (
              <div className="charts-grid">
                <div className="chart-card">
                  <h4>Check-in split</h4>
                  <div className="chart-box">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={statusPieData} dataKey="value" nameKey="name" innerRadius={52} outerRadius={80} paddingAngle={3}>
                          <Cell fill="var(--brand)" />
                          <Cell fill="var(--line-strong)" />
                        </Pie>
                        <Tooltip contentStyle={tooltipStyle} />
                        <Legend wrapperStyle={{ fontSize: "0.82rem" }} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div className="chart-card">
                  <h4>Tickets by tier</h4>
                  {tierChartData.length === 0 ? (
                    <p className="muted-text">No tier data yet.</p>
                  ) : (
                    <div className="chart-box">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={tierChartData} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                          <XAxis dataKey="tier" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} />
                          <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
                          <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--surface-soft)" }} />
                          <Legend wrapperStyle={{ fontSize: "0.82rem" }} />
                          <Bar dataKey="Issued" fill="var(--line-strong)" radius={[6, 6, 0, 0]} />
                          <Bar dataKey="Checked" fill="var(--brand)" radius={[6, 6, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
                <div className="chart-card chart-span">
                  <h4>Check-ins by gate</h4>
                  {gateChartData.length === 0 ? (
                    <p className="muted-text">No gates added for this event yet.</p>
                  ) : (
                    <div className="chart-box">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={gateChartData} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
                          <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                          <XAxis dataKey="name" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} interval={0} />
                          <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
                          <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--surface-soft)" }} />
                          <Bar dataKey="Checked" fill="var(--brand)" radius={[6, 6, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              </div>
            )}
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
              <h3>Waitlist</h3>
              <span className="sync-state">{(stats.waitlist || []).length} waiting</span>
            </div>
            {(!stats.waitlist || stats.waitlist.length === 0) ? (
              <p className="muted-text">No attendees currently on the waitlist.</p>
            ) : (
              <div className="stats-table-wrap">
                <table className="stats-table">
                  <thead>
                    <tr>
                      <th style={{ width: "80px" }}>Position</th>
                      <th>Attendee</th>
                      <th>Tier</th>
                      <th>Contact</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.waitlist.map((entry) => (
                      <tr key={entry.id}>
                        <td>
                          <strong>#{entry.position}</strong>
                        </td>
                        <td>
                          <strong>{entry.attendee_name}</strong>
                          {entry.campus_id && <small className="stats-sub">{entry.campus_id}</small>}
                        </td>
                        <td>
                          <span className="capitalize">{entry.tier}</span>
                        </td>
                        <td>
                          <span>{entry.attendee_contact}</span>
                        </td>
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

            {revokeError && <p className="error-text">{revokeError}</p>}
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
                      <th>Action</th>
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
                        <td>
                          <div style={{ display: "inline-flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                            <button
                              type="button"
                              className="ghost-button revoke-button"
                              style={{ padding: "4px 8px", fontSize: "0.8rem", color: "var(--danger, #dc2626)" }}
                              disabled={ticket.status === "revoked" || revokingId === ticket.ticket_id}
                              onClick={() => handleRevoke(ticket.ticket_id)}
                            >
                              {revokingId === ticket.ticket_id ? "Revoking..." : "Revoke"}
                            </button>
                            {ticket.status === "revoked" && (
                              <span className="inline-revoked-confirm" style={{ fontSize: "0.8rem", color: "var(--danger, #dc2626)", fontWeight: 600 }}>
                                Revoked
                                {promotedInfo[ticket.ticket_id] && (
                                  <span className="inline-promoted-confirm" style={{ color: "var(--text-muted, #475569)", marginLeft: "4px", fontWeight: 500 }}>
                                    {` -- seat given to ${promotedInfo[ticket.ticket_id].attendee_name} from the waitlist.`}
                                  </span>
                                )}
                              </span>
                            )}
                          </div>
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
