import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Clock, Copy, Download, Maximize2, Printer, Share2, TicketCheck, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

import { isTxPayload, normalizeTxPayload } from "../lib/ticketQr";

// The QR is an SVG, so it stays crisp at any size. 320px leaves roughly a
// 298px code after the white frame's padding, which is the desktop target;
// on a 375px phone the frame shrinks to the card width but stays above 220px.
const QR_SCREEN_SIZE = 320;
const QR_OVERLAY_SIZE = 480;
const QR_FG = "#000000";
const QR_BG = "#ffffff";

function safeFileName(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

function drawWrappedText(context, text, x, y, maxWidth, lineHeight, maxLines = 2) {
  const words = text.split(/\s+/);
  const lines = [];
  let currentLine = "";

  for (const word of words) {
    const candidate = currentLine ? `${currentLine} ${word}` : word;
    if (context.measureText(candidate).width <= maxWidth || !currentLine) {
      currentLine = candidate;
    } else {
      lines.push(currentLine);
      currentLine = word;
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }

  lines.slice(0, maxLines).forEach((line, index) => {
    const isTruncated = index === maxLines - 1 && lines.length > maxLines;
    context.fillText(isTruncated ? `${line.replace(/[.,;:]?$/, "")}...` : line, x, y + index * lineHeight);
  });
}

/** Split the exact payload into visual groups without inserting characters. */
function codeChunks(value, size = 4) {
  const chunks = [];
  for (let index = 0; index < value.length; index += size) {
    chunks.push(value.slice(index, index + size));
  }
  return chunks;
}

/** Break a long unbroken string at character boundaries for the PNG pass. */
function wrapFixedText(context, text, maxWidth, maxLines = 3) {
  const lines = [];
  let currentLine = "";

  for (const character of text) {
    const candidate = currentLine + character;
    if (currentLine && context.measureText(candidate).width > maxWidth) {
      lines.push(currentLine);
      currentLine = character;
    } else {
      currentLine = candidate;
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }

  if (lines.length <= maxLines) {
    return lines;
  }
  const kept = lines.slice(0, maxLines);
  kept[maxLines - 1] = `${kept[maxLines - 1].slice(0, -1)}…`;
  return kept;
}

function ManualCode({ code, copyState, onCopy, compact = false }) {
  return (
    <div className={`manual-code${compact ? " manual-code--compact" : ""}`}>
      <p className="manual-code-label">Manual code (use this if the QR won&apos;t scan)</p>
      <code className="manual-code-value" title={code || "No manual code"}>
        {codeChunks(code).map((chunk, index) => (
          <span className="manual-code-chunk" key={`${chunk}-${index}`}>
            {chunk}
          </span>
        ))}
      </code>
      <button className="ghost-button manual-code-copy" type="button" onClick={onCopy} disabled={!code}>
        {copyState === "copied" ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
        {copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy code"}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {copyState === "copied"
          ? "Manual code copied to the clipboard."
          : copyState === "failed"
            ? "Could not copy the manual code."
            : ""}
      </span>
    </div>
  );
}

function QrBlock({ ticket, txPayload, triggerRef, onOpen, qrRef, copyState, onCopy, compact = false }) {
  const seatTitle = `seat ${ticket.seat_number} at ${ticket.event.title}`;

  return (
    <div className={`pass-qr-block${compact ? " pass-qr-block--compact" : ""}`}>
      <div className="pass-seat-line">
        <span className="pass-tier">{ticket.tier}</span>
        <strong className="pass-seat">{ticket.seat_number}</strong>
      </div>
      <button
        className="qr-open-button"
        type="button"
        ref={triggerRef}
        onClick={onOpen}
        aria-label={`Open a larger QR code for ${seatTitle}`}
        aria-haspopup="dialog"
      >
        <span className="qr-frame" ref={qrRef}>
          <QRCodeSVG
            value={txPayload}
            size={QR_SCREEN_SIZE}
            level="M"
            bgColor={QR_BG}
            fgColor={QR_FG}
            marginSize={4}
            title={`QR code for ${seatTitle}`}
          />
        </span>
        <span className="qr-open-hint">
          <Maximize2 size={14} aria-hidden="true" />
          Tap to enlarge
        </span>
      </button>
      <ManualCode code={txPayload} copyState={copyState} onCopy={onCopy} compact={compact} />
    </div>
  );
}

function QrOverlay({ ticket, txPayload, copyState, onCopy, onClose, titleId }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = dialogRef.current?.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      const items = focusable ? Array.from(focusable) : [];
      if (items.length === 0) return;

      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  const seatTitle = `seat ${ticket.seat_number} at ${ticket.event.title}`;

  return createPortal(
    <div
      className="qr-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="qr-overlay-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialogRef}
      >
        <button
          ref={closeRef}
          className="icon-button qr-overlay-close"
          type="button"
          onClick={onClose}
          aria-label="Close larger QR code"
        >
          <X size={22} aria-hidden="true" />
        </button>
        <p className="eyebrow">Scan at the gate</p>
        <h2 id={titleId}>{ticket.event.title}</h2>
        <div className="pass-seat-line pass-seat-line--overlay">
          <span className="pass-tier">{ticket.tier}</span>
          <strong className="pass-seat">{ticket.seat_number}</strong>
        </div>
        <div className="qr-frame qr-frame--overlay">
          <QRCodeSVG
            value={txPayload}
            size={QR_OVERLAY_SIZE}
            level="M"
            bgColor={QR_BG}
            fgColor={QR_FG}
            marginSize={4}
            title={`Large QR code for ${seatTitle}`}
          />
        </div>
        <ManualCode code={txPayload} copyState={copyState} onCopy={onCopy} />
        <p className="qr-overlay-hint">Raise your screen brightness</p>
      </div>
    </div>,
    document.body,
  );
}

export function TicketPreview({ ticket, compact = false }) {
  const qrRef = useRef(null);
  const triggerRef = useRef(null);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [copyState, setCopyState] = useState("idle");
  const [summaryState, setSummaryState] = useState("idle");
  const [shareState, setShareState] = useState("idle");

  const txPayload = normalizeTxPayload(ticket?.qr_signature);
  const txValid = isTxPayload(txPayload);
  const overlayTitleId = `qr-overlay-title-${ticket?.ticket_id ?? "ticket"}`;

  const closeOverlay = useCallback(() => {
    setOverlayOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      try {
        const area = document.createElement("textarea");
        area.value = value;
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        return true;
      } catch {
        return false;
      }
    }
  }

  function flash(setter, value) {
    setter(value);
    window.setTimeout(() => setter("idle"), 1600);
  }

  async function handleCopyTx() {
    if (!txPayload) return;
    flash(setCopyState, (await copyText(txPayload)) ? "copied" : "failed");
  }

  function buildGateSummary() {
    const eventDate = new Date(ticket.event.date_time).toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    return [
      `TicketX pass: ${ticket.event.title}`,
      `Attendee: ${ticket.attendee.name}`,
      `Date: ${eventDate}`,
      `Venue: ${ticket.event.venue}`,
      `Tier: ${ticket.tier.toUpperCase()} / Seat: ${ticket.seat_number}`,
      `Ticket #${ticket.ticket_id} / Status: ${ticket.status}`,
      `TX: ${txPayload}`,
    ].join("\n");
  }

  async function handleCopySummary() {
    flash(setSummaryState, (await copyText(buildGateSummary())) ? "copied" : "failed");
  }

  async function handleShare() {
    if (!txValid) return;
    const shareData = {
      title: `TicketX pass: ${ticket.event.title}`,
      text: buildGateSummary(),
    };
    if (typeof navigator.share === "function") {
      try {
        await navigator.share(shareData);
        flash(setShareState, "shared");
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        flash(setShareState, (await copyText(shareData.text)) ? "copied" : "failed");
      }
      return;
    }
    flash(setShareState, (await copyText(shareData.text)) ? "copied" : "failed");
  }

  function handlePrint() {
    window.print();
  }

  if (!ticket) {
    return (
      <section className="panel ticket-preview empty-state">
        <TicketCheck aria-hidden="true" />
        <h2>No ticket issued yet</h2>
        <p>Register an attendee to generate a signed QR ticket for the gate.</p>
      </section>
    );
  }

  if (ticket.outcome === "waitlisted") {
    return (
      <section className="panel ticket-preview waitlist-state">
        <div className="ticket-copy">
          <p className="eyebrow">Waitlist Confirmed</p>
          <h2>{ticket.event?.title || "Event Waitlist"}</h2>
          {ticket.attendee?.name && <p>{ticket.attendee.name}</p>}
        </div>
        <div className="waitlist-card">
          <Clock size={40} aria-hidden="true" />
          <p className="waitlist-message">
            This event is full. You've been added to the waitlist at position #{ticket.position}.
          </p>
          <p className="muted-text">
            No QR code or seat is assigned while you wait. If a seat opens up it goes to the
            person at the front of the line, and your ticket appears in your waitlist panel.
          </p>
        </div>
      </section>
    );
  }

  // A revoked pass must never present a scannable credential. My Tickets
  // already renders its own revoked card; this guard covers any other caller.
  if (ticket.status === "revoked") {
    return (
      <section className="panel ticket-preview ticket-preview--revoked">
        <div className="ticket-copy">
          <p className="eyebrow">Ticket revoked</p>
          <h2>{ticket.event.title}</h2>
          <p>{ticket.attendee.name}</p>
          <p className="muted-text">This ticket is no longer valid. No QR code or manual code is shown.</p>
        </div>
        <span className="status-pill revoked">Revoked</span>
      </section>
    );
  }

  async function handleDownload() {
    const qrElement = qrRef.current?.querySelector("svg");
    if (!qrElement) {
      setDownloadError("The QR image is not ready yet.");
      return;
    }
    setDownloadError("");
    const source = new XMLSerializer().serializeToString(qrElement);
    const qrBlob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
    const qrUrl = URL.createObjectURL(qrBlob);

    try {
      const qrImage = new Image();
      qrImage.src = qrUrl;
      await new Promise((resolve, reject) => {
        qrImage.onload = resolve;
        qrImage.onerror = () => reject(new Error("Could not prepare the QR image."));
      });

      const canvas = document.createElement("canvas");
      canvas.width = 1400;
      canvas.height = 800;
      const context = canvas.getContext("2d");

      context.fillStyle = "#e8f0ee";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#ffffff";
      context.fillRect(42, 42, 1316, 716);
      context.fillStyle = "#0d8068";
      context.fillRect(42, 42, 18, 716);

      context.fillStyle = "#0d8068";
      context.font = "700 30px Arial, sans-serif";
      context.fillText("TICKETX", 100, 112);
      context.fillStyle = "#5f716d";
      context.font = "700 19px Arial, sans-serif";
      context.fillText("SECURE EVENT PASS", 100, 150);

      context.fillStyle = "#14201d";
      context.font = "700 48px Arial, sans-serif";
      drawWrappedText(context, ticket.event.title, 100, 246, 700, 58, 2);

      const eventDate = new Date(ticket.event.date_time).toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
      const details = [
        ["ATTENDEE", ticket.attendee.name],
        ["DATE", eventDate],
        ["VENUE", ticket.event.venue],
        ["TIER", ticket.tier.toUpperCase()],
        ["SEAT", ticket.seat_number],
      ];

      details.forEach(([label, value], index) => {
        const column = index % 2;
        const row = Math.floor(index / 2);
        const x = 100 + column * 360;
        const y = 390 + row * 112;
        context.fillStyle = "#70817c";
        context.font = "700 17px Arial, sans-serif";
        context.fillText(label, x, y);
        context.fillStyle = "#14201d";
        context.font = "700 25px Arial, sans-serif";
        drawWrappedText(context, String(value), x, y + 38, 315, 30, 2);
      });

      // The QR takes roughly half the pass height. The white panel behind it
      // keeps the quiet zone intact even if the pass is printed on tinted paper.
      context.fillStyle = "#f4f8f7";
      context.fillRect(875, 82, 420, 636);
      context.textAlign = "center";
      context.fillStyle = "#5f716d";
      context.font = "700 16px Arial, sans-serif";
      context.fillText(`TICKET #${ticket.ticket_id}`, 1085, 116);
      context.fillStyle = "#14201d";
      context.font = "700 34px Arial, sans-serif";
      context.fillText(ticket.seat_number, 1085, 162);
      context.fillStyle = "#0d8068";
      context.font = "700 18px Arial, sans-serif";
      context.fillText(`${ticket.tier.toUpperCase()} SEATING`, 1085, 190);

      context.fillStyle = "#ffffff";
      context.fillRect(895, 208, 380, 380);
      context.drawImage(qrImage, 905, 218, 360, 360);

      context.fillStyle = "#5f716d";
      context.font = "700 13px Arial, sans-serif";
      context.fillText("MANUAL CODE (IF QR FAILS)", 1085, 618);
      context.fillStyle = "#14201d";
      context.font = "600 14px 'Courier New', monospace";
      const codeLines = wrapFixedText(context, txPayload, 370, 3);
      codeLines.forEach((line, index) => {
        context.fillText(line, 1085, 642 + index * 18);
      });
      context.textAlign = "left";

      const pngBlob = await new Promise((resolve, reject) => {
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Could not create the ticket image."))), "image/png");
      });
      const downloadUrl = URL.createObjectURL(pngBlob);
      const link = document.createElement("a");
      link.href = downloadUrl;
      link.download = `ticketx-${safeFileName(ticket.event.title)}-ticket-${ticket.ticket_id}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(downloadUrl);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Could not download the ticket.");
    } finally {
      URL.revokeObjectURL(qrUrl);
    }
  }

  if (compact) {
    const statusLabel =
      ticket.status === "issued" ? "Issued" : ticket.status === "used" ? "Used" : ticket.status === "revoked" ? "Revoked" : ticket.status;
    const statusClass = ticket.status === "issued" ? "in" : ticket.status === "used" ? "out" : "revoked";

    return (
      <>
        <section className="panel ticket-preview ticket-preview--compact">
          <div className="ticket-compact-head">
            <p className="eyebrow">Seat pass</p>
            <span className={`status-pill ${statusClass}`}>{statusLabel}</span>
          </div>
          <div className="ticket-compact-copy">
            <h3>{ticket.event.title}</h3>
            <p>{ticket.attendee.name}</p>
          </div>
          {txValid ? (
            <QrBlock
              ticket={ticket}
              txPayload={txPayload}
              triggerRef={triggerRef}
              onOpen={() => setOverlayOpen(true)}
              qrRef={qrRef}
              copyState={copyState}
              onCopy={handleCopyTx}
              compact
            />
          ) : (
            <p className="error-text" role="alert">
              TX missing — please re-issue this ticket.
            </p>
          )}
          {downloadError && <p className="error-text ticket-download-error">{downloadError}</p>}
          <div className="ticket-actions">
            <button className="ghost-button" type="button" onClick={handleDownload} disabled={!txValid}>
              <Download size={18} aria-hidden="true" />
              Download ticket
            </button>
          </div>
        </section>
        {overlayOpen && txValid && (
          <QrOverlay
            ticket={ticket}
            txPayload={txPayload}
            copyState={copyState}
            onCopy={handleCopyTx}
            onClose={closeOverlay}
            titleId={overlayTitleId}
          />
        )}
      </>
    );
  }

  return (
    <>
      <section className="panel ticket-preview ticket-print-area">
        <div className="ticket-copy">
          <p className="eyebrow">My Ticket</p>
          <h2>{ticket.event.title}</h2>
          <p>{ticket.attendee.name}</p>
          <dl className="ticket-meta">
            <div>
              <dt>Venue</dt>
              <dd>{ticket.event.venue}</dd>
            </div>
            <div>
              <dt>Tier</dt>
              <dd>{ticket.tier}</dd>
            </div>
            <div>
              <dt>Seat</dt>
              <dd>{ticket.seat_number}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{ticket.status}</dd>
            </div>
          </dl>
        </div>
        {txValid ? (
          <QrBlock
            ticket={ticket}
            txPayload={txPayload}
            triggerRef={triggerRef}
            onOpen={() => setOverlayOpen(true)}
            qrRef={qrRef}
            copyState={copyState}
            onCopy={handleCopyTx}
          />
        ) : (
          <p className="error-text" role="alert">
            TX missing — please re-issue this ticket.
          </p>
        )}
        {!txValid && (
          <p className="error-text tx-hint" role="alert">
            This ticket has no valid TX payload. Re-issue it before heading to the gate.
          </p>
        )}
        <p className="muted-text tx-hint">Scan this QR or type the manual code at the gate. Both use the same TX.</p>
        <div className="gate-summary">
          <div className="section-heading">
            <p className="eyebrow">Gate Summary</p>
            <h3>Show this if the camera fails</h3>
          </div>
          <pre className="gate-summary-text">{buildGateSummary()}</pre>
          <button className="ghost-button" type="button" onClick={handleCopySummary} aria-label="Copy gate summary">
            {summaryState === "copied" ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
            {summaryState === "copied" ? "Summary copied" : summaryState === "failed" ? "Copy failed" : "Copy summary"}
          </button>
        </div>
        {downloadError && <p className="error-text ticket-download-error">{downloadError}</p>}
        <div className="ticket-actions">
          <button className="ghost-button" type="button" onClick={handleDownload} disabled={!txValid}>
            <Download size={18} aria-hidden="true" />
            Download ticket
          </button>
          <button className="ghost-button" type="button" onClick={handlePrint}>
            <Printer size={18} aria-hidden="true" />
            Print
          </button>
          <button className="ghost-button" type="button" onClick={handleShare} disabled={!txValid}>
            <Share2 size={18} aria-hidden="true" />
            {shareState === "shared" ? "Shared" : shareState === "copied" ? "Copied" : shareState === "failed" ? "Share failed" : "Share"}
          </button>
        </div>
      </section>
      {overlayOpen && txValid && (
        <QrOverlay
          ticket={ticket}
          txPayload={txPayload}
          copyState={copyState}
          onCopy={handleCopyTx}
          onClose={closeOverlay}
          titleId={overlayTitleId}
        />
      )}
    </>
  );
}
