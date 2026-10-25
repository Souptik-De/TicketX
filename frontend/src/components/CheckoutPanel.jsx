import { useCallback, useEffect, useRef, useState } from "react";
import { Clock, Send, TicketCheck } from "lucide-react";
import { createPortal } from "react-dom";

import { confirmHold, getHold, getMyTickets } from "../lib/api";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RESYNC_MS = 30000;
const RESYNC_TOLERANCE_SECONDS = 2;

function formatCountdown(totalSeconds) {
  const safe = Math.max(0, Math.ceil(Number(totalSeconds) || 0));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/**
 * The checkout step for a live hold.
 *
 * The server owns time: `seconds_remaining` seeds a monotonic deadline, and the
 * local tick only interpolates between server answers. Every resync is a fresh
 * `GET /holds/{id}`, so a suspended tab cannot "gain" hold time by sleeping.
 *
 * All request guards are refs rather than state so a double click or a slow
 * response cannot slip a second confirm past React's render cycle.
 */
export function CheckoutPanel({
  event,
  hold,
  sessionUser,
  resumeNotice = "",
  isCancelling = false,
  onCancel,
  onConfirmed,
  onExpired,
  onUnauthorized,
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [campusId, setCampusId] = useState("");
  const [error, setError] = useState("");
  const [isConfirming, setIsConfirming] = useState(false);
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.ceil(Number(hold.seconds_remaining) || 0)));
  const [announcement, setAnnouncement] = useState("");

  const mountedRef = useRef(true);
  const deadlineRef = useRef(0);
  const syncingRef = useRef(false);
  const expiryCheckRef = useRef(false);
  const confirmingRef = useRef(false);
  const expiredRef = useRef(false);
  const onConfirmedRef = useRef(onConfirmed);
  const onExpiredRef = useRef(onExpired);
  const onUnauthorizedRef = useRef(onUnauthorized);
  onConfirmedRef.current = onConfirmed;
  onExpiredRef.current = onExpired;
  onUnauthorizedRef.current = onUnauthorized;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Prefill from the account, but leave the fields editable: this form has
  // always been able to book someone other than the account holder.
  useEffect(() => {
    if (sessionUser) {
      setName((current) => current || sessionUser.display_name || "");
      setEmail((current) => current || sessionUser.email || "");
    }
  }, [sessionUser]);

  // A new hold restarts the deadline. The initial server value is the only
  // source of truth; the monotonic clock is used solely to avoid counting a
  // suspended tab as elapsed hold time.
  useEffect(() => {
    const seconds = Math.max(0, Math.ceil(Number(hold.seconds_remaining) || 0));
    deadlineRef.current = performance.now() + seconds * 1000;
    expiryCheckRef.current = false;
    expiredRef.current = false;
    setRemaining(seconds);
  }, [hold.hold_id, hold.seconds_remaining]);

  const expire = useCallback(() => {
    if (expiredRef.current) return;
    expiredRef.current = true;
    onExpiredRef.current?.();
  }, []);

  const syncHold = useCallback(
    async ({ atZero = false } = {}) => {
      if (syncingRef.current || !mountedRef.current) return;
      syncingRef.current = true;
      try {
        const fresh = await getHold(hold.hold_id);
        if (!mountedRef.current) return;
        if (fresh.status !== "active") {
          expire();
          return;
        }

        const serverSeconds = Math.max(1, Math.ceil(Number(fresh.seconds_remaining) || 0));
        const localSeconds = Math.max(0, Math.ceil((deadlineRef.current - performance.now()) / 1000));
        if (atZero || Math.abs(serverSeconds - localSeconds) > RESYNC_TOLERANCE_SECONDS) {
          deadlineRef.current = performance.now() + serverSeconds * 1000;
          setRemaining(serverSeconds);
        }
        expiryCheckRef.current = false;
      } catch (syncError) {
        if (!mountedRef.current) return;
        if (syncError?.status === 401) {
          onUnauthorizedRef.current?.();
          return;
        }
        if (syncError?.status === 404) {
          expire();
          return;
        }
        // A transient failure keeps the local timer. At zero, try again next tick.
        if (atZero) {
          expiryCheckRef.current = false;
        }
      } finally {
        syncingRef.current = false;
      }
    },
    [hold.hold_id, expire],
  );

  useEffect(() => {
    function tick() {
      if (document.hidden) return;
      const next = Math.max(0, Math.ceil((deadlineRef.current - performance.now()) / 1000));
      setRemaining((current) => (current === next ? current : next));
      if (next === 0 && !expiryCheckRef.current) {
        expiryCheckRef.current = true;
        syncHold({ atZero: true });
      }
    }

    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [syncHold]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) syncHold();
    }, RESYNC_MS);
    return () => window.clearInterval(timer);
  }, [syncHold]);

  useEffect(() => {
    function handleVisibility() {
      if (!document.hidden) syncHold();
    }
    function handlePageShow(pageEvent) {
      if (pageEvent.persisted) syncHold();
    }
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("pageshow", handlePageShow);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, [syncHold]);

  // Only two announcements, ever. A live region that changes every second is
  // unusable with a screen reader.
  useEffect(() => {
    if (remaining === 60) {
      setAnnouncement("60 seconds remaining");
    } else if (remaining === 10) {
      setAnnouncement("10 seconds remaining");
    }
  }, [remaining]);

  async function handleAlreadyConfirmed() {
    let tickets = [];
    try {
      const all = await getMyTickets();
      tickets = (all ?? []).filter((ticket) => ticket.event?.id === event?.id && ticket.status !== "revoked");
    } catch (ticketError) {
      if (ticketError?.status === 401) {
        onUnauthorizedRef.current?.();
        return;
      }
      // The booking is still confirmed; the success screen can link to My Tickets.
    }
    if (!mountedRef.current) return;
    onConfirmedRef.current?.(tickets, { alreadyConfirmed: true });
  }

  async function handleConfirm(submitEvent) {
    submitEvent.preventDefault();
    if (confirmingRef.current) return;

    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedCampusId = campusId.trim();

    setError("");
    if (trimmedName.length < 2) {
      setError("Enter the attendee's full name.");
      return;
    }
    if (!trimmedEmail || !EMAIL_PATTERN.test(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }

    confirmingRef.current = true;
    setIsConfirming(true);
    try {
      const result = await confirmHold(hold.hold_id, {
        attendee_name: trimmedName,
        attendee_contact: trimmedEmail,
        campus_id: trimmedCampusId || undefined,
      });
      if (!mountedRef.current) return;
      onConfirmedRef.current?.(result.tickets ?? [], { alreadyConfirmed: false });
    } catch (confirmError) {
      if (!mountedRef.current) return;

      if (confirmError?.status === 401) {
        onUnauthorizedRef.current?.();
        return;
      }

      if (confirmError?.status === 409) {
        const message = String(confirmError.message || "").toLowerCase();
        if (message.includes("hold expired")) {
          expire();
          return;
        }
        if (message.includes("already confirmed")) {
          await handleAlreadyConfirmed();
          return;
        }
        // Any other conflict leaves the hold alone so the attendee can retry.
        setError(confirmError.message || "Could not confirm this booking.");
        return;
      }

      // A 400/422 means the request was rejected before the hold changed. Keep
      // the form exactly as typed and never run the expiry flow for it.
      if (confirmError?.status === 400 || confirmError?.status === 422) {
        setError(confirmError.message || "Could not confirm this booking.");
        return;
      }

      // Network/5xx: the confirm may have landed. Ask the server instead of
      // assuming either outcome.
      try {
        const fresh = await getHold(hold.hold_id);
        if (!mountedRef.current) return;
        if (fresh.status === "confirmed") {
          await handleAlreadyConfirmed();
          return;
        }
        if (fresh.status !== "active") {
          expire();
          return;
        }
        setError(confirmError.message || "Could not confirm this booking. Check your connection and try again.");
      } catch (holdError) {
        if (!mountedRef.current) return;
        if (holdError?.status === 401) {
          onUnauthorizedRef.current?.();
          return;
        }
        if (holdError?.status === 404) {
          expire();
          return;
        }
        setError("Could not confirm this booking. Check your connection and try again.");
      }
    } finally {
      confirmingRef.current = false;
      if (mountedRef.current) setIsConfirming(false);
    }
  }

  const isWarning = remaining < 60;

  return (
    <div className="checkout">
      <div className="checkout-head">
        <div>
          <p className="eyebrow">Checkout</p>
          <h2>{event?.title ?? "Your booking"}</h2>
          {event && (
            <p className="muted-text">
              {event.venue} · {event.capacity} seats
            </p>
          )}
        </div>
        <div className={`checkout-timer ${isWarning ? "is-warning" : ""}`}>
          <Clock size={18} aria-hidden="true" />
          <span>Time left</span>
          <strong role="timer" aria-live="off">
            {formatCountdown(remaining)}
          </strong>
        </div>
        <span className="sr-only" role="status" aria-live="polite">
          {announcement}
        </span>
      </div>

      {resumeNotice && (
        <p className="success-text" role="status">
          {resumeNotice}
        </p>
      )}

      <div className="checkout-seats" aria-label="Held seats">
        <p className="eyebrow">Your seats</p>
        <ul>
          {hold.seats.map((seat) => (
            <li key={seat.id}>
              <strong>{seat.label}</strong>
              <span>{seat.tier_name}</span>
            </li>
          ))}
        </ul>
        <p className="muted-text">One set of attendee details and one QR pass per seat.</p>
      </div>

      <form className="ticket-form checkout-form" id="checkout-form" onSubmit={handleConfirm}>
        <label>
          Attendee name
          <input
            value={name}
            onChange={(changeEvent) => setName(changeEvent.target.value)}
            placeholder="Enter attendee full name"
            required
            minLength={2}
            autoComplete="name"
          />
        </label>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(changeEvent) => setEmail(changeEvent.target.value)}
            placeholder="Enter email address"
            required
            autoComplete="email"
          />
        </label>
        <label>
          Campus ID
          <input
            value={campusId}
            onChange={(changeEvent) => setCampusId(changeEvent.target.value)}
            placeholder="Optional campus ID"
          />
        </label>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="checkout-actions">
          <button className="primary-button checkout-confirm" type="submit" disabled={isConfirming || isCancelling}>
            <Send size={18} aria-hidden="true" />
            {isConfirming ? "Confirming..." : "Confirm booking"}
          </button>
          <button className="ghost-button" type="button" onClick={onCancel} disabled={isConfirming || isCancelling}>
            <TicketCheck size={18} aria-hidden="true" />
            {isCancelling ? "Releasing seats..." : "Cancel and choose different seats"}
          </button>
        </div>
      </form>

      {createPortal(
        <div className="booking-bar booking-bar--checkout">
          <div className="booking-bar-countdown">
            <span>Time left</span>
            <strong aria-hidden="true">{formatCountdown(remaining)}</strong>
          </div>
          <button className="primary-button" type="submit" form="checkout-form" disabled={isConfirming || isCancelling}>
            {isConfirming ? "Confirming..." : "Confirm"}
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}
