import { useRef, useState } from "react";
import { Download, TicketCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

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

export function TicketPreview({ ticket }) {
  const qrRef = useRef(null);
  const [downloadError, setDownloadError] = useState("");

  if (!ticket) {
    return (
      <section className="panel ticket-preview empty-state">
        <TicketCheck aria-hidden="true" />
        <h2>No ticket issued yet</h2>
        <p>Register an attendee to generate a signed QR ticket for the gate.</p>
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

      context.fillStyle = "#f4f8f7";
      context.fillRect(875, 82, 420, 636);
      context.fillStyle = "#14201d";
      context.font = "700 22px Arial, sans-serif";
      context.textAlign = "center";
      context.fillText(`TICKET #${ticket.ticket_id}`, 1085, 135);
      context.drawImage(qrImage, 915, 170, 340, 340);
      context.fillStyle = "#0d8068";
      context.font = "700 38px Arial, sans-serif";
      context.fillText(ticket.seat_number, 1085, 570);
      context.fillStyle = "#5f716d";
      context.font = "600 18px Arial, sans-serif";
      context.fillText(`${ticket.tier.toUpperCase()} SEATING`, 1085, 606);
      context.font = "600 16px Arial, sans-serif";
      context.fillText("Present this QR at the assigned event gate", 1085, 670);
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

  return (
    <section className="panel ticket-preview">
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
      <div className="qr-wrap" ref={qrRef} aria-label="Ticket QR code">
        <QRCodeSVG value={ticket.qr_signature} size={180} level="M" includeMargin />
        <span>Ticket #{ticket.ticket_id}</span>
        <strong>{ticket.seat_number}</strong>
      </div>
      {downloadError && <p className="error-text ticket-download-error">{downloadError}</p>}
      <button className="ghost-button" type="button" onClick={handleDownload}>
        <Download size={18} aria-hidden="true" />
        Download ticket
      </button>
    </section>
  );
}
