import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

const fmtTime = (iso) =>
  new Date(iso.endsWith("Z") ? iso : iso + "Z").toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Kolkata",
  });

export function ScanResultCard({ result }) {
  if (!result) {
    return (
      <section className="panel scan-result waiting">
        <AlertTriangle aria-hidden="true" />
        <h2>Waiting for scan</h2>
        <p>Scan a signed TicketX QR payload to see the gate decision.</p>
      </section>
    );
  }

  const isValid = result.result === "valid";
  const isDuplicate = result.result === "duplicate";
  const Icon = isValid ? CheckCircle2 : isDuplicate ? AlertTriangle : XCircle;

  return (
    <section className={`panel scan-result ${result.result}`}>
      <Icon aria-hidden="true" />
      <p className="eyebrow">Scan Result</p>
      <h2>{isValid ? "Valid ticket" : isDuplicate ? "Already used" : "Invalid ticket"}</h2>
      <p>{result.message}</p>
      {result.attendee_name && (
        <dl className="result-meta">
          <div>
            <dt>Attendee</dt>
            <dd>{result.attendee_name}</dd>
          </div>
          <div>
            <dt>Tier</dt>
            <dd>{result.tier}</dd>
          </div>
          <div>
            <dt>Seat</dt>
            <dd>{result.seat_number}</dd>
          </div>
          {result.scanned_at && (
            <div>
              <dt>Scanned at</dt>
              <dd>{fmtTime(result.scanned_at)}</dd>
            </div>
          )}
          {result.prior_scan && (
            <div>
              <dt>First scan</dt>
              <dd>
                {result.prior_scan.gate_name}, {fmtTime(result.prior_scan.timestamp)}
              </dd>
            </div>
          )}
        </dl>
      )}
    </section>
  );
}
