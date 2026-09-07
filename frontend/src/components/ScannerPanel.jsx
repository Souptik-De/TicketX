import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera, ScanLine } from "lucide-react";
import { Html5Qrcode } from "html5-qrcode";

import { scanTicket } from "../lib/api";
import { extractTxPayload, normalizeTxPayload } from "../lib/ticketQr";

function describeCameraError(err) {
  const name = err?.name ?? "";
  if (name === "NotAllowedError") return "Camera permission denied. Allow camera access, or paste the TX payload instead.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No suitable camera found. Paste the TX payload instead.";
  if (name === "NotSupportedError" || name === "SecurityError") return "Camera needs HTTPS or localhost. Paste the TX payload instead.";
  if (name === "NotReadableError") return "Camera is busy in another tab/app. Close it and retry, or paste the TX.";
  return "Camera is unavailable. Paste the TX payload instead.";
}

export function ScannerPanel({ gates, volunteers, onScanComplete, onGateRefresh }) {
  const firstGate = gates[0];
  const [qrValue, setQrValue] = useState("");
  const [gateId, setGateId] = useState(firstGate?.id ?? "");
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const scannerRef = useRef(null);
  const decodedOnceRef = useRef(false);

  useEffect(() => {
    setGateId((current) => current || firstGate?.id || "");
  }, [firstGate]);

  const selectedVolunteer = useMemo(() => {
    return volunteers.find((volunteer) => volunteer.gate_id === Number(gateId)) ?? volunteers[0];
  }, [gateId, volunteers]);

  const validateTx = useCallback(
    async (rawTx) => {
      const tx = normalizeTxPayload(rawTx);
      if (!tx) {
        setError("Paste or scan a QR payload before validating.");
        return;
      }
      if (!gateId || !selectedVolunteer) {
        setError("Select a gate with an assigned volunteer before scanning.");
        return;
      }
      setError("");
      setIsValidating(true);
      try {
        const result = await scanTicket({
          qr_signature: tx,
          gate_id: Number(gateId),
          volunteer_id: selectedVolunteer.id,
        });
        onScanComplete(result);
        onGateRefresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Scan failed");
      } finally {
        setIsValidating(false);
      }
    },
    [gateId, selectedVolunteer, onScanComplete, onGateRefresh],
  );

  async function stopScanner() {
    const scanner = scannerRef.current;
    scannerRef.current = null;
    if (!scanner) return;
    try {
      if (scanner.isScanning) {
        await scanner.stop();
      }
    } catch {
      // Already stopped - safe to ignore.
    }
    try {
      scanner.clear();
    } catch {
      // DOM already cleaned up - safe to ignore.
    }
  }

  useEffect(() => {
    if (!cameraEnabled) return;
    let cancelled = false;
    decodedOnceRef.current = false;

    async function startScanner() {
      setIsStarting(true);
      setError("");
      setInfo("");
      // Ensure any previous instance is fully torn down before creating a new one.
      await stopScanner();
      if (cancelled) return;

      const scanner = new Html5Qrcode("qr-reader", { verbose: false });
      scannerRef.current = scanner;

      const config = { fps: 10, qrbox: { width: 250, height: 250 }, aspectRatio: 1.0 };
      const onSuccess = (decodedText) => {
        if (decodedOnceRef.current) return;
        decodedOnceRef.current = true;
        const tx = extractTxPayload(decodedText);
        setQrValue(tx);
        setInfo(`Scanned ${tx.slice(0, 18)}… validating…`);
        setCameraEnabled(false);
        validateTx(tx);
      };

      try {
        // Prefer back/environment camera when multiple cameras exist.
        let startTarget = { facingMode: "environment" };
        try {
          const cameras = await Html5Qrcode.getCameras();
          const back = cameras.find((c) => /back|rear|environment/i.test(c.label));
          const pick = back ?? cameras[0];
          if (pick?.id) startTarget = pick.id;
        } catch {
          // getCameras() can throw before permission is granted - fall back to facingMode.
        }
        if (cancelled) return;
        await scanner.start(startTarget, config, onSuccess, () => undefined);
        if (cancelled) {
          await stopScanner();
          return;
        }
      } catch (err) {
        if (!cancelled) {
          setError(describeCameraError(err));
          setCameraEnabled(false);
        }
      } finally {
        if (!cancelled) setIsStarting(false);
      }
    }

    startScanner();
    return () => {
      cancelled = true;
      stopScanner();
      setIsStarting(false);
    };
  }, [cameraEnabled]);

  // Stop camera on unmount.
  useEffect(() => {
    return () => {
      stopScanner();
    };
  }, []);

  async function handleScan(event) {
    event.preventDefault();
    await validateTx(qrValue);
  }

  return (
    <section className="panel scanner-panel">
      <div className="section-heading">
        <p className="eyebrow">Gate Scanner</p>
        <h2>Validate Entry</h2>
      </div>
      <div className="scanner-actions">
        <button
          className="ghost-button"
          type="button"
          disabled={isStarting}
          onClick={() => {
            setError("");
            setInfo("");
            if (cameraEnabled) {
              setCameraEnabled(false);
            } else {
              decodedOnceRef.current = false;
              setCameraEnabled(true);
            }
          }}
        >
          <Camera size={18} aria-hidden="true" />
          {isStarting ? "Starting camera…" : cameraEnabled ? "Stop camera" : "Use camera"}
        </button>
      </div>
      <div id="qr-reader" className="qr-reader" style={{ display: cameraEnabled ? "block" : "none" }} />
      {!cameraEnabled && !qrValue && (
        <p className="muted-text">Tip: open the camera and point it at the ticket QR, or paste the copied TX.</p>
      )}
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
        {info && <p className="muted-text">{info}</p>}
        {error && <p className="error-text">{error}</p>}
        <button className="primary-button" type="submit" disabled={isValidating}>
          <ScanLine size={18} aria-hidden="true" />
          {isValidating ? "Validating…" : "Validate ticket"}
        </button>
      </form>
    </section>
  );
}
