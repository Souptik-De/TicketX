import { useCallback, useEffect, useMemo, useState } from "react";
import { Armchair, Hourglass, RefreshCw, TicketCheck } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { getMyTicketStats } from "../lib/api";

export function UserStats({ refreshKey }) {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError("");
    try {
      setStats(await getMyTicketStats());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your stats");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const tierChartData = useMemo(() => {
    if (!stats) return [];
    return stats.tiers.map((row) => ({
      tier: row.tier.charAt(0).toUpperCase() + row.tier.slice(1),
      Tickets: row.count,
    }));
  }, [stats]);

  const statusPieData = useMemo(() => {
    if (!stats) return [];
    return [
      { name: "Checked in", value: stats.used },
      { name: "Still valid", value: stats.valid },
    ];
  }, [stats]);

  const tooltipStyle = {
    backgroundColor: "var(--surface-solid)",
    border: "1px solid var(--line)",
    borderRadius: "8px",
    color: "var(--text)",
    fontSize: "0.85rem",
  };

  if (isLoading && !stats) {
    return (
      <div className="panel">
        <p className="muted-text">Loading your stats...</p>
      </div>
    );
  }

  if (error && !stats) {
    return null;
  }

  if (!stats || stats.total === 0) {
    return null;
  }

  return (
    <div className="panel">
      <div className="section-heading row-heading">
        <div>
          <p className="eyebrow">Your history</p>
          <h2>My stats</h2>
        </div>
        <button className="ghost-button stats-filter" type="button" onClick={load} disabled={isLoading} title="Refresh stats">
          <RefreshCw size={15} aria-hidden="true" />
          {isLoading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      <div className="admin-metrics">
        <div>
          <Armchair size={20} aria-hidden="true" />
          <span>Tickets held</span>
          <strong>{stats.total}</strong>
        </div>
        <div>
          <TicketCheck size={20} aria-hidden="true" />
          <span>Checked in</span>
          <strong>{stats.used}</strong>
        </div>
        <div>
          <Hourglass size={20} aria-hidden="true" />
          <span>Waitlisted</span>
          <strong>{stats.waitlisted}</strong>
        </div>
      </div>

      <div className="charts-grid">
        <div className="chart-card">
          <h4>Used vs valid</h4>
          <div className="chart-box chart-box-sm">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={statusPieData} dataKey="value" nameKey="name" innerRadius={44} outerRadius={68} paddingAngle={3}>
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
          <div className="chart-box chart-box-sm">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={tierChartData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="tier" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--surface-soft)" }} />
                <Bar dataKey="Tickets" fill="var(--brand)" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="stats-section">
        <div className="row-heading">
          <h3>Ticket history</h3>
          <span className="sync-state">{stats.tickets.length} shown</span>
        </div>
        <div className="stats-table-wrap">
          <table className="stats-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Ticket</th>
                <th>Status</th>
                <th>Checked at</th>
              </tr>
            </thead>
            <tbody>
              {stats.tickets.map((item) => (
                <tr key={item.ticket_id}>
                  <td>
                    <strong>{item.event_title}</strong>
                    <small className="stats-sub">
                      {item.venue}
                      {item.event_date_time ? ` - ${new Date(item.event_date_time).toLocaleString()}` : ""}
                    </small>
                  </td>
                  <td>
                    <span className="capitalize">
                      {item.tier} - {item.seat_number}
                    </span>
                    <small className="stats-sub">#{item.ticket_id}</small>
                  </td>
                  <td>
                    <span className={`status-pill ${item.checked_in ? "in" : "out"}`}>
                      {item.checked_in ? "Used" : "Valid"}
                    </span>
                    <small className="stats-sub">{item.status}</small>
                  </td>
                  <td>
                    {item.checked_in ? (
                      <>
                        <strong>{item.check_gate_name ?? "Gate"}</strong>
                        {item.checked_at && (
                          <small className="stats-sub">{new Date(item.checked_at).toLocaleString()}</small>
                        )}
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
      </div>
    </div>
  );
}
