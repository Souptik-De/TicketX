import { useCallback, useEffect, useMemo, useState } from "react";
import { DoorOpen, RefreshCw, ScanLine, TicketCheck, TriangleAlert } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { getMyScans } from "../lib/api";

function resultPill(result) {
  if (result === "valid") return { className: "in", label: "Accepted" };
  if (result === "duplicate") return { className: "dup", label: "Duplicate" };
  return { className: "bad", label: "Rejected" };
}

export function ScannerStats({ refreshSignal }) {
  const [stats, setStats] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError("");
    try {
      setStats(await getMyScans());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your scan stats");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (refreshSignal) {
      load();
    }
  }, [refreshSignal, load]);

  const gateChartData = useMemo(() => {
    if (!stats) return [];
    return stats.gates.map((gate) => ({
      name: gate.name.length > 14 ? `${gate.name.slice(0, 13)}…` : gate.name,
      Scanned: gate.scanned_count,
    }));
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
        <p className="muted-text">Loading your scan stats...</p>
      </div>
    );
  }

  if (error && !stats) {
    return null;
  }

  if (!stats || stats.total === 0) {
    return (
      <div className="panel">
        <div className="section-heading">
          <p className="eyebrow">Your scanning</p>
          <h2>My scans</h2>
        </div>
        <p className="muted-text">No scans yet. Tickets you validate will show up here with totals per gate.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="section-heading row-heading">
        <div>
          <p className="eyebrow">Your scanning</p>
          <h2>My scans</h2>
        </div>
        <button className="ghost-button stats-filter" type="button" onClick={load} disabled={isLoading} title="Refresh scan stats">
          <RefreshCw size={15} aria-hidden="true" />
          {isLoading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      <div className="admin-metrics">
        <div>
          <ScanLine size={20} aria-hidden="true" />
          <span>Total scans</span>
          <strong>{stats.total}</strong>
        </div>
        <div>
          <TicketCheck size={20} aria-hidden="true" />
          <span>Accepted</span>
          <strong>{stats.valid}</strong>
        </div>
        <div>
          <TriangleAlert size={20} aria-hidden="true" />
          <span>Duplicates</span>
          <strong>{stats.duplicate}</strong>
        </div>
      </div>

      {gateChartData.length > 0 && (
        <div className="chart-card">
          <h4>Accepted per gate</h4>
          <div className="chart-box chart-box-sm">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={gateChartData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={{ stroke: "var(--line)" }} tickLine={false} interval={0} />
                <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "var(--surface-soft)" }} />
                <Bar dataKey="Scanned" fill="var(--brand)" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="stats-section">
        <div className="row-heading">
          <h3>Scan history</h3>
          <span className="sync-state">{stats.recent.length} recent</span>
        </div>
        <div className="stats-table-wrap">
          <table className="stats-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Result</th>
                <th>Gate</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent.map((item) => {
                const pill = resultPill(item.result);
                return (
                  <tr key={item.scan_id}>
                    <td>
                      <strong>{item.attendee_name}</strong>
                      <small className="stats-sub">
                        <span className="capitalize">{item.tier} - {item.seat_number}</span>
                        {` - ${item.event_title}`}
                      </small>
                    </td>
                    <td>
                      <span className={`status-pill ${pill.className}`}>{pill.label}</span>
                    </td>
                    <td>
                      <strong>
                        <DoorOpen size={14} aria-hidden="true" /> {item.gate_name}
                      </strong>
                      <small className="stats-sub">{new Date(item.timestamp).toLocaleString()}</small>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
