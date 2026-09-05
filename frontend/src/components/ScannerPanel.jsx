import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, ScanLine } from "lucide-react";
import { Html5Qrcode } from "html5-qrcode";

import { scanTicket } from "../lib/api";

export function ScannerPanel({ gates, volunteers, onScanComplete, onGateRefresh }) {
  const firstGate = gates[0];
  const [qrValue, setQrValue] = useState("");
  const [gateId, setGateId] = useState(firstGate?.id ?? "");
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [error, setError] = useState("");
  const scannerRef = useRef(null);

  useEffect(() => {
    setGateId((current) => current || firstGate?.id || "");
  }, [firstGate]);

  const selectedVolunteer = useMemo(() => {
    return volunteers.find((volunteer) => volunteer.gate_id === Number(gateId)) ?? volunteers[0];
  }, [gateId, volunteers]);

  useEffect(() => {
    if (!cameraEnabled) {
      scannerRef.current?.stop().catch(() => undefined);
      scannerRef.current = null;
      return;
    }

    const scanner = new Html5Qrcode("qr-reader");
    scannerRef.current = scanner;
    scanner
      .start(
        { facingMode: "environment" },
        { fps: 10, qrbox: { width: 220, height: 220 } },
        (decodedText) => {
          setQrValue(decodedText);
          setCameraEnabled(false);
        },
        () => undefined,
      )
      .catch(() => {
        setError("Camera is unavailable. Paste the QR payload instead.");
        setCameraEnabled(false);
      });

    return () => {
      scanner.stop().catch(() => undefined);
    };
  }, [cameraEnabled]);

  async function handleScan(event) {
    event.preventDefault();
    setError("");

    if (!qrValue.trim()) {
      setError("Paste or scan a QR payload before validating.");
      return;
    }

    if (!gateId || !selectedVolunteer) {
      setError("Select a gate with an assigned volunteer before scanning.");
      return;
    }

    try {
      const result = await scanTicket({
        qr_signature: qrValue.trim(),
        gate_id: Number(gateId),
        volunteer_id: selectedVolunteer.id,
      });
      onScanComplete(result);
      onGateRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed");
    }
  }

  return (
    <section className="panel scanner-panel">
      <div className="section-heading">
        <p className="eyebrow">Gate Scanner</p>
        <h2>Validate Entry</h2>
      </div>
      <div className="scanner-actions">
        <button className="ghost-button" type="button" onClick={() => setCameraEnabled((value) => !value)}>
          <Camera size={18} aria-hidden="true" />
          {cameraEnabled ? "Stop camera" : "Use camera"}
        </button>
      </div>
      {cameraEnabled && <div id="qr-reader" className="qr-reader" />}
      <form className="scan-form" onSubmit={handleScan}>
        <label>
          Gate
          <select value={gateId} onChange={(event) => setGateId(Number(event.target.value))} required>
            {gates.map((gate) => (
              <option key={gate.id} value={gate.id}>
                {gate.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Volunteer
          <input value={selectedVolunteer?.name ?? "No volunteer assigned"} readOnly />
        </label>
        <label className="full-width">
          QR payload
          <textarea
            value={qrValue}
            onChange={(event) => setQrValue(event.target.value)}
            placeholder="Scan or paste TX-..."
            rows={3}
          />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="primary-button" type="submit">
          <ScanLine size={18} aria-hidden="true" />
          Validate ticket
        </button>
      </form>
    </section>
  );
}
