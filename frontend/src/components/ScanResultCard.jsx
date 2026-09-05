import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

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
  const Icon = isValid ? CheckCircle2 : XCircle;

  return (
    <section className={`panel scan-result ${result.result}`}>
      <Icon aria-hidden="true" />
      <p className="eyebrow">Scan Result</p>
      <h2>{isValid ? "Valid ticket" : "Invalid ticket"}</h2>
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
        </dl>
      )}
    </section>
  );
}
