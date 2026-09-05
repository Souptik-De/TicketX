import { DoorOpen, RefreshCw, Wifi } from "lucide-react";

function formatSyncTime(value) {
  return value ? new Date(value).toLocaleTimeString() : "No scans yet";
}

export function GateStatus({ gates, onRefresh }) {
  return (
    <section className="panel gate-status">
      <div className="section-heading row-heading">
        <div>
          <p className="eyebrow">Live Operations</p>
          <h2>Gate Sync Status</h2>
        </div>
        <button className="icon-button" type="button" onClick={onRefresh} aria-label="Refresh gate status">
          <RefreshCw size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="gate-list">
        {gates.length === 0 && (
          <div className="empty-inline">
            <DoorOpen size={20} aria-hidden="true" />
            <span>No gates have been added for this event.</span>
          </div>
        )}
        {gates.map((gate) => (
          <article className="gate-row" key={gate.gate_id}>
            <div>
              <h3>{gate.name}</h3>
              <p>{gate.location}</p>
            </div>
            <div className="gate-number">
              <strong>{gate.scanned_count}</strong>
              <span>checked in</span>
            </div>
            <div className="sync-state">
              <Wifi size={16} aria-hidden="true" />
              <span>{formatSyncTime(gate.last_synced)}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
