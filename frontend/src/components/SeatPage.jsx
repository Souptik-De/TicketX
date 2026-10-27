import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Armchair, ArrowLeft, CalendarDays, MapPin, RefreshCw } from "lucide-react";

import { cancelHold, createHold, getEventSeats, getHold, getMyTickets } from "../lib/api";
import {
  clearHoldId,
  clearWaitlistState,
  readHoldId,
  readWaitlistState,
  writeHoldId,
  writeWaitlistState,
} from "../lib/bookingStorage";
import { formatDate } from "../lib/format";
import { BookingSuccess } from "./BookingSuccess";
import { CheckoutPanel } from "./CheckoutPanel";
import { SeatMap } from "./SeatMap";
import { WaitlistPanel } from "./WaitlistPanel";

// The map is shared, so it changes without anyone touching this page: someone
// else can hold or buy a seat at any moment. Five seconds is the brief's
// balance between "feels live" and "does not hammer the API".
const POLL_MS = 5000;
const MAX_SEATS = 6;
const NAV_CANCEL_MS = 1500;

function seatIsSelectable(seat, allowMine) {
  return seat.state === "available" || (allowMine && seat.mine && seat.state === "held");
}

function collectSelectableIds(map, allowMine) {
  const ids = new Set();
  for (const tier of map?.tiers ?? []) {
    for (const row of tier.rows ?? []) {
      for (const block of row.blocks ?? []) {
        for (const seat of block) {
          if (seatIsSelectable(seat, allowMine)) {
            ids.add(seat.id);
          }
        }
      }
    }
  }
  return ids;
}

function mapHasMineSeats(map) {
  return (map?.tiers ?? []).some((tier) =>
    (tier.rows ?? []).some((row) => (row.blocks ?? []).some((block) => block.some((seat) => seat.mine))),
  );
}

function removalMessage(seats) {
  const labels = seats.map((seat) => seat.label);
  if (labels.length === 1) {
    return `Seat ${labels[0]} was just taken and removed from your selection`;
  }
  return `Seats ${labels.join(", ")} were just taken and removed from your selection`;
}

function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((resolve, reject) => {
    timer = window.setTimeout(() => reject(new Error("Timed out")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => window.clearTimeout(timer));
}

/**
 * Seat booking for one event.
 *
 * Four states share one page:
 *   map      -- live polling, selection, and the waitlist when the room is full
 *   checkout -- a live hold with a server-backed countdown
 *   success  -- the confirmed passes
 *   waitlist -- not a separate state; it is the map when zero seats are free
 *
 * The precedence on load is deliberate: a stored hold resumes first, then seats
 * already held by this account are offered back (another device), and only then
 * does a genuinely full room open the waitlist.
 */
export function SeatPage({
  event,
  isEventLoading = false,
  sessionUser,
  onBack,
  onUnauthorized,
  onViewTickets,
  onBooked,
  onCheckoutGuardChange,
  onWaitlistJoined,
  liveWaitlistEntry = null,
  isWaitlistLoaded = false,
}) {
  const [map, setMap] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [phase, setPhase] = useState("map");
  const [selectedSeats, setSelectedSeats] = useState([]);
  const [notice, setNotice] = useState("");
  const [hold, setHold] = useState(null);
  const [isHolding, setIsHolding] = useState(false);
  const [tickets, setTickets] = useState([]);
  const [alreadyConfirmed, setAlreadyConfirmed] = useState(false);
  const [resumeNotice, setResumeNotice] = useState("");
  const [resumeError, setResumeError] = useState("");
  const [isResuming, setIsResuming] = useState(false);
  const [resumeAttempt, setResumeAttempt] = useState(0);
  const [waitlistEntry, setWaitlistEntry] = useState(null);
  const [isCancelling, setIsCancelling] = useState(false);

  const canBook = sessionUser?.role === "user";
  const userId = sessionUser?.id ?? null;
  const eventId = event?.id ?? null;

  const mountedRef = useRef(false);
  const stoppedRef = useRef(false);
  const selectedSeatsRef = useRef([]);
  const phaseRef = useRef(phase);
  const holdRef = useRef(hold);
  const canBookRef = useRef(canBook);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onCheckoutGuardChangeRef = useRef(onCheckoutGuardChange);
  const holdControllerRef = useRef(null);
  const cancelControllerRef = useRef(null);
  const refetchControllerRef = useRef(null);
  const holdingRef = useRef(false);
  const cancellingRef = useRef(false);

  phaseRef.current = phase;
  holdRef.current = hold;
  canBookRef.current = canBook;
  selectedSeatsRef.current = selectedSeats;
  onUnauthorizedRef.current = onUnauthorized;
  onCheckoutGuardChangeRef.current = onCheckoutGuardChange;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(
    () => () => {
      refetchControllerRef.current?.abort();
      holdControllerRef.current?.abort();
      cancelControllerRef.current?.abort();
    },
    [],
  );

  // A new event (or a new account in the same tab) starts clean. The stored
  // waitlist membership is per user and event, so it is read here rather than
  // kept from a previous event.
  useEffect(() => {
    setMap(null);
    setIsLoading(true);
    setError("");
    setIsReconnecting(false);
    setPhase("map");
    setHold(null);
    setTickets([]);
    setAlreadyConfirmed(false);
    setSelectedSeats([]);
    selectedSeatsRef.current = [];
    setNotice("");
    setResumeNotice("");
    setResumeError("");
    setIsResuming(false);
    setIsCancelling(false);
    setWaitlistEntry(readWaitlistState(userId, eventId));
    stoppedRef.current = false;
  }, [eventId, userId]);

  // The queue is server state, so a promotion or a withdrawal has to win over
  // whatever this browser remembered. App polls `/me/registrations`; when its
  // answer for this event changes, the banner follows it. A promoted entry whose
  // ticket was later revoked is treated as gone, so the join form comes back.
  useEffect(() => {
    // Wait for the first server answer before trusting or discarding the stored
    // place. After that, an empty answer means the entry was withdrawn or
    // otherwise resolved, so localStorage must not keep showing it.
    if (!isWaitlistLoaded) return;

    if (!liveWaitlistEntry) {
      setWaitlistEntry((current) => (current ? null : current));
      clearWaitlistState(userId, eventId);
      return;
    }

    const { status, position, waitlist_entry_id: entryId, promoted_ticket: promotedTicket } = liveWaitlistEntry;

    if (status === "waiting") {
      const next = { position, entryId, status: "waiting" };
      setWaitlistEntry((current) =>
        current?.position === next.position && current?.entryId === next.entryId && current?.status === next.status
          ? current
          : next,
      );
      writeWaitlistState(userId, eventId, next);
      return;
    }

    if (status === "promoted" && promotedTicket && promotedTicket.status !== "revoked") {
      const next = { position: null, entryId, status: "promoted" };
      setWaitlistEntry((current) => (current?.entryId === next.entryId && current?.status === next.status ? current : next));
      return;
    }

    // Cancelled, or promoted then revoked: the queue place no longer exists.
    setWaitlistEntry((current) => (current ? null : current));
    clearWaitlistState(userId, eventId);
  }, [liveWaitlistEntry, isWaitlistLoaded, userId, eventId]);

  // A stored hold is the first thing that wins. The id is the only handle the
  // API gives us; without it another device can only see the `mine` seats.
  useEffect(() => {
    if (!eventId || !canBook || !userId) {
      setIsResuming(false);
      return undefined;
    }

    const storedId = readHoldId(userId, eventId);
    if (!storedId) {
      setIsResuming(false);
      setResumeError("");
      return undefined;
    }

    let cancelled = false;
    const controller = new AbortController();
    setIsResuming(true);
    setResumeError("");

    getHold(storedId, { signal: controller.signal })
      .then((fresh) => {
        if (cancelled || !mountedRef.current) return;
        if (fresh.status === "active" && fresh.seconds_remaining > 0 && (fresh.seats?.length ?? 0) > 0) {
          setHold(fresh);
          setPhase("checkout");
          setResumeNotice("Resumed your checkout");
          setNotice("");
        } else {
          clearHoldId(userId, eventId);
        }
      })
      .catch((resumeFailure) => {
        if (cancelled || resumeFailure?.name === "AbortError") return;
        if (resumeFailure?.status === 401) {
          stoppedRef.current = true;
          onUnauthorizedRef.current?.(eventId);
          return;
        }
        if (resumeFailure?.status === 404) {
          clearHoldId(userId, eventId);
          return;
        }
        // Keep the id: a network blip must not throw away a live checkout.
        setResumeError(resumeFailure?.message || "Could not resume your checkout.");
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) {
          setIsResuming(false);
        }
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [eventId, canBook, userId, resumeAttempt]);

  /**
   * Apply a fresh map and reconcile the selection with it.
   *
   * A selection that disappears because someone else took the seat is never
   * silent. `silentRemoval` exists only for the callers that show a more
   * specific message themselves (a 409 from Proceed, for example).
   */
  const applyMap = useCallback((data, { silentRemoval = false } = {}) => {
    const selectable = collectSelectableIds(data, canBookRef.current);
    const current = selectedSeatsRef.current;
    const removed = current.filter((seat) => !selectable.has(seat.id));

    if (removed.length > 0) {
      const next = current.filter((seat) => selectable.has(seat.id));
      selectedSeatsRef.current = next;
      setSelectedSeats(next);
      if (!silentRemoval) {
        setNotice(removalMessage(removed));
      }
    }

    setMap(data);
  }, []);

  // Poll only while the map is the visible step. Checkout and success pause it:
  // an open hold is not helped by re-rendering the map underneath, and the
  // server already owns the timer.
  useEffect(() => {
    if (!eventId || phase !== "map" || stoppedRef.current) return undefined;

    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let controller = null;
    let lastPayload = "";

    async function load() {
      if (cancelled || stopped || inFlight || document.hidden) return;
      inFlight = true;
      controller = new AbortController();

      try {
        const data = await getEventSeats(eventId, { signal: controller.signal });
        if (cancelled || stopped) return;

        setIsReconnecting(false);
        setIsLoading(false);

        const serialized = JSON.stringify(data);
        if (serialized !== lastPayload) {
          lastPayload = serialized;
          applyMap(data);
        }
      } catch (requestError) {
        if (cancelled || requestError?.name === "AbortError") return;
        setIsLoading(false);

        if (requestError?.status === 401) {
          stopped = true;
          stoppedRef.current = true;
          onUnauthorizedRef.current?.(eventId);
          return;
        }

        if (requestError?.status === 404 || requestError?.status === 409) {
          stopped = true;
          setIsReconnecting(false);
          setError(requestError.message || "This event does not have a seat map.");
          return;
        }

        // Anything else (offline, 500) is worth retrying; keep the last map.
        setIsReconnecting(true);
      } finally {
        inFlight = false;
        controller = null;
      }
    }

    load();
    const timer = window.setInterval(load, POLL_MS);

    function handleVisibility() {
      if (!document.hidden) load();
    }
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibility);
      controller?.abort();
    };
  }, [eventId, phase, applyMap]);

  const refetchMap = useCallback(async () => {
    if (!eventId) return null;
    refetchControllerRef.current?.abort();
    const controller = new AbortController();
    refetchControllerRef.current = controller;

    try {
      const data = await getEventSeats(eventId, { signal: controller.signal });
      if (!mountedRef.current) return null;
      applyMap(data, { silentRemoval: true });
      setIsLoading(false);
      setError("");
      return data;
    } catch (requestError) {
      if (requestError?.name === "AbortError") return null;
      if (requestError?.status === 401) {
        stoppedRef.current = true;
        onUnauthorizedRef.current?.(eventId);
      }
      return null;
    } finally {
      if (refetchControllerRef.current === controller) {
        refetchControllerRef.current = null;
      }
    }
  }, [eventId, applyMap]);

  const seatIndex = useMemo(() => {
    const index = new Map();
    for (const tier of map?.tiers ?? []) {
      for (const row of tier.rows ?? []) {
        for (const block of row.blocks ?? []) {
          for (const seat of block) {
            index.set(seat.id, { seat, tierName: tier.name });
          }
        }
      }
    }
    return index;
  }, [map]);

  function handleToggle(seat) {
    if (!canBook || phaseRef.current !== "map" || isResuming) return;
    const meta = seatIndex.get(seat.id);
    if (!meta) return;

    const current = selectedSeatsRef.current;
    const exists = current.some((selected) => selected.id === seat.id);
    let next;

    if (exists) {
      next = current.filter((selected) => selected.id !== seat.id);
    } else {
      if (current.length >= MAX_SEATS) {
        setNotice("You can select at most 6 seats");
        return;
      }
      next = [...current, { id: seat.id, label: seat.label, tierName: meta.tierName }];
    }

    selectedSeatsRef.current = next;
    setSelectedSeats(next);
    setNotice("");
  }

  function handleClear() {
    selectedSeatsRef.current = [];
    setSelectedSeats([]);
    setNotice("");
  }

  async function handleProceed() {
    if (!canBook || holdingRef.current || isResuming || selectedSeatsRef.current.length === 0) return;

    holdingRef.current = true;
    setIsHolding(true);
    setNotice("");
    setResumeError("");

    const controller = new AbortController();
    holdControllerRef.current = controller;

    try {
      const created = await createHold(
        eventId,
        selectedSeatsRef.current.map((seat) => seat.id),
        { signal: controller.signal },
      );
      if (!mountedRef.current || stoppedRef.current) return;

      writeHoldId(userId, eventId, created.hold_id);
      setHold(created);
      setResumeNotice("");
      setPhase("checkout");
    } catch (requestError) {
      if (!mountedRef.current || requestError?.name === "AbortError") return;

      if (requestError?.status === 401) {
        stoppedRef.current = true;
        onUnauthorizedRef.current?.(eventId);
        return;
      }

      if (requestError?.status === 409) {
        await refetchMap();
        if (mountedRef.current && !stoppedRef.current) {
          setNotice("Seat no longer available. Someone else just took it. Nothing was held.");
        }
        return;
      }

      // A 400 keeps the selection: the attendee can fix the count and retry.
      setNotice(requestError?.message || "Could not hold those seats. Check your connection and try again.");
    } finally {
      holdingRef.current = false;
      if (mountedRef.current) setIsHolding(false);
      if (holdControllerRef.current === controller) {
        holdControllerRef.current = null;
      }
    }
  }

  const finishConfirmed = useCallback(
    (confirmedTickets, { alreadyConfirmed: wasAlreadyConfirmed = false } = {}) => {
      if (!mountedRef.current) return;
      clearHoldId(userId, eventId);
      setTickets(confirmedTickets ?? []);
      setAlreadyConfirmed(Boolean(wasAlreadyConfirmed));
      setHold(null);
      setPhase("success");
      setResumeNotice("");
      setNotice("");
      onBooked?.(confirmedTickets ?? []);
    },
    [eventId, onBooked, userId],
  );

  const loadConfirmedTickets = useCallback(async () => {
    try {
      const all = await getMyTickets();
      return (all ?? []).filter((ticket) => ticket.event?.id === eventId && ticket.status !== "revoked");
    } catch (requestError) {
      if (requestError?.status === 401) {
        stoppedRef.current = true;
        onUnauthorizedRef.current?.(eventId);
      }
      return [];
    }
  }, [eventId]);

  function handleExpired() {
    if (!mountedRef.current) return;
    clearHoldId(userId, eventId);
    setHold(null);
    setTickets([]);
    setAlreadyConfirmed(false);
    setResumeNotice("");
    setPhase("map");
    setSelectedSeats([]);
    selectedSeatsRef.current = [];
    setNotice("Your hold expired and the seats were released");
    refetchMap();
  }

  async function handleCancel() {
    if (cancellingRef.current) return;

    const holdId = holdRef.current?.hold_id;
    if (!holdId) {
      setPhase("map");
      return;
    }

    cancellingRef.current = true;
    setIsCancelling(true);
    const controller = new AbortController();
    cancelControllerRef.current = controller;
    let failureMessage = "";

    try {
      await cancelHold(holdId, { signal: controller.signal });
    } catch (requestError) {
      if (requestError?.name === "AbortError") return;

      if (requestError?.status === 401) {
        stoppedRef.current = true;
        onUnauthorizedRef.current?.(eventId);
        return;
      }

      if (
        requestError?.status === 409 &&
        String(requestError.message || "").toLowerCase().includes("already confirmed")
      ) {
        const confirmedTickets = await loadConfirmedTickets();
        finishConfirmed(confirmedTickets, { alreadyConfirmed: true });
        return;
      }

      failureMessage = "We couldn't confirm the release; the seats free up automatically when the timer ends.";
    } finally {
      cancellingRef.current = false;
      if (mountedRef.current) setIsCancelling(false);
      if (cancelControllerRef.current === controller) {
        cancelControllerRef.current = null;
      }
    }

    if (!mountedRef.current) return;
    clearHoldId(userId, eventId);
    setHold(null);
    setTickets([]);
    setAlreadyConfirmed(false);
    setResumeNotice("");
    setPhase("map");
    setSelectedSeats([]);
    selectedSeatsRef.current = [];
    setNotice(failureMessage);
    refetchMap();
  }

  // Used by App's topbar navigation and by Back. It is capped so a slow cancel
  // cannot trap the attendee on the checkout screen.
  const cancelForNavigation = useCallback(async () => {
    if (phaseRef.current !== "checkout") return true;

    const holdId = holdRef.current?.hold_id;
    if (!holdId) return true;

    const controller = new AbortController();
    cancelControllerRef.current = controller;
    let alreadyConfirmed = false;

    try {
      await withTimeout(cancelHold(holdId, { signal: controller.signal }), NAV_CANCEL_MS);
    } catch (requestError) {
      if (requestError?.status === 401) {
        stoppedRef.current = true;
        onUnauthorizedRef.current?.(eventId);
        return false;
      }
      if (
        requestError?.status === 409 &&
        String(requestError.message || "").toLowerCase().includes("already confirmed")
      ) {
        alreadyConfirmed = true;
      }
      // A timeout or network failure still lets the navigation continue; the
      // server's expiry timer is the backstop.
    } finally {
      if (cancelControllerRef.current === controller) {
        cancelControllerRef.current = null;
      }
    }

    if (!mountedRef.current) return true;
    clearHoldId(userId, eventId);

    if (alreadyConfirmed) {
      const confirmedTickets = await loadConfirmedTickets();
      if (!mountedRef.current) return false;
      finishConfirmed(confirmedTickets, { alreadyConfirmed: true });
      return false;
    }

    setHold(null);
    setPhase("map");
    setSelectedSeats([]);
    selectedSeatsRef.current = [];
    setResumeNotice("");
    return true;
  }, [eventId, finishConfirmed, loadConfirmedTickets, userId]);

  useEffect(() => {
    if (phase === "checkout" && hold) {
      onCheckoutGuardChangeRef.current?.(cancelForNavigation);
    } else {
      onCheckoutGuardChangeRef.current?.(null);
    }
    return () => {
      onCheckoutGuardChangeRef.current?.(null);
    };
  }, [phase, hold, cancelForNavigation]);

  // Deliberately no release on `pagehide`. A refresh fires it too, so cancelling
  // there would make the stored hold impossible to resume. The server's expiry
  // timer is the real guarantee; closing the tab simply lets it run out.

  async function handleBack() {
    if (phaseRef.current === "checkout") {
      const proceed = await cancelForNavigation();
      if (!proceed) return;
    }
    onBack?.();
  }

  function handleBookMore() {
    setPhase("map");
    setTickets([]);
    setAlreadyConfirmed(false);
    setSelectedSeats([]);
    selectedSeatsRef.current = [];
    setNotice("");
    refetchMap();
  }

  function handleWaitlistJoined(entry) {
    writeWaitlistState(userId, eventId, entry);
    setWaitlistEntry(entry);
    onWaitlistJoined?.();
  }

  function handleSeatsAvailable() {
    setNotice("Seats are available again. Choose a seat.");
    refetchMap();
  }

  const groupedSeats = useMemo(() => {
    const groups = new Map();
    for (const seat of selectedSeats) {
      if (!groups.has(seat.tierName)) {
        groups.set(seat.tierName, []);
      }
      groups.get(seat.tierName).push(seat.label);
    }
    return Array.from(groups.entries());
  }, [selectedSeats]);

  const hasMineSeats = useMemo(() => mapHasMineSeats(map), [map]);
  const showWaitlist =
    canBook &&
    phase === "map" &&
    Boolean(map) &&
    map.available === 0 &&
    !hasMineSeats &&
    !isResuming &&
    !resumeError;
  const showMineNotice =
    canBook && phase === "map" && Boolean(map) && hasMineSeats && !hold && !isResuming;

  if (!event) {
    return (
      <section className="seat-page">
        <div className="panel seat-page-panel">
          <div className="seat-page-head">
            <h2>{isEventLoading ? "Loading event..." : "Event not found"}</h2>
            <button className="ghost-button" type="button" onClick={onBack}>
              <ArrowLeft size={16} aria-hidden="true" />
              Back
            </button>
          </div>
          {isEventLoading ? (
            <p className="muted-text">Fetching the event details...</p>
          ) : (
            <p className="banner-error">This event is no longer available. It may have been deleted.</p>
          )}
        </div>
      </section>
    );
  }

  return (
    <section className={`seat-page ${phase === "checkout" ? "seat-page--checkout" : ""}`} aria-labelledby="seat-page-title">
      <div className="panel seat-page-panel">
        <div className="seat-page-head">
          <div className="seat-page-intro">
            <p className="eyebrow">Seat map</p>
            <h2 id="seat-page-title">{event.title}</h2>
            <div className="seat-page-meta">
              <span>
                <CalendarDays size={16} aria-hidden="true" />
                {formatDate(event.date_time)}
              </span>
              <span>
                <MapPin size={16} aria-hidden="true" />
                {event.venue}
              </span>
              <span>
                <Armchair size={16} aria-hidden="true" />
                {event.capacity} seats
              </span>
            </div>
          </div>
          <button className="ghost-button" type="button" onClick={handleBack}>
            <ArrowLeft size={16} aria-hidden="true" />
            Back
          </button>
        </div>

        {phase === "map" && (
          <p className="seat-page-note">
            {canBook ? "Choose up to 6 seats." : "View only"}
          </p>
        )}

        {isResuming && (
          <p className="seat-page-status" role="status">
            <RefreshCw size={15} aria-hidden="true" />
            Checking your checkout...
          </p>
        )}

        {resumeError && (
          <div className="banner-error" role="alert">
            <p>{resumeError}</p>
            <button className="ghost-button" type="button" onClick={() => setResumeAttempt((current) => current + 1)}>
              Retry
            </button>
          </div>
        )}

        {isReconnecting && (
          <p className="seat-page-status" role="status">
            <RefreshCw size={15} aria-hidden="true" />
            Reconnecting...
          </p>
        )}

        {error && (
          <div className="banner-error" role="alert">
            {error}
          </div>
        )}

        {notice && (
          <p className="seat-page-notice" role="status">
            {notice}
          </p>
        )}

        {showMineNotice && (
          <p className="seat-page-notice" role="status">
            You have seats on hold from another session. They are released automatically when the timer ends, or
            choose seats again to replace them.
          </p>
        )}

        {phase === "map" && (
          <>
            {showWaitlist && (
              <WaitlistPanel
                event={event}
                sessionUser={sessionUser}
                joined={waitlistEntry}
                onJoined={handleWaitlistJoined}
                onSeatsAvailable={handleSeatsAvailable}
                onUnauthorized={() => onUnauthorizedRef.current?.(eventId)}
                onViewTickets={onViewTickets}
              />
            )}

            {isLoading && !map && !error && <p className="muted-text">Loading the seat map...</p>}

            {map && (
              <SeatMap
                tiers={map.tiers}
                selectedIds={selectedSeats.map((seat) => seat.id)}
                onToggle={handleToggle}
                readOnly={!canBook || isResuming}
                allowMineSelection={canBook && !isResuming}
              />
            )}
          </>
        )}
      </div>

      {canBook && phase === "map" && !showWaitlist && (
        <div className="selection-summary">
          <p className="selection-summary-count">
            Selected: <strong>{selectedSeats.length}</strong>
          </p>
          <div className="selection-summary-tiers">
            {groupedSeats.length === 0 ? (
              <span className="muted-text">Pick a seat on the map.</span>
            ) : (
              groupedSeats.map(([tier, labels]) => (
                <span key={tier}>
                  <strong>{tier}:</strong> {labels.join(", ")}
                </span>
              ))
            )}
          </div>
          <div className="selection-summary-actions">
            <button
              className="ghost-button"
              type="button"
              onClick={handleClear}
              disabled={selectedSeats.length === 0 || isHolding || isResuming}
            >
              Clear
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={handleProceed}
              disabled={selectedSeats.length === 0 || isHolding || isResuming}
            >
              {isHolding ? "Holding..." : "Proceed to checkout"}
            </button>
          </div>
        </div>
      )}

      {phase === "checkout" && hold && (
        <div className="panel seat-page-panel">
          <CheckoutPanel
            event={event}
            hold={hold}
            sessionUser={sessionUser}
            resumeNotice={resumeNotice}
            isCancelling={isCancelling}
            onCancel={handleCancel}
            onConfirmed={finishConfirmed}
            onExpired={handleExpired}
            onUnauthorized={() => onUnauthorizedRef.current?.(eventId)}
          />
        </div>
      )}

      {phase === "success" && (
        <BookingSuccess
          event={event}
          tickets={tickets}
          alreadyConfirmed={alreadyConfirmed}
          onViewTickets={onViewTickets}
          onBookMore={handleBookMore}
        />
      )}
    </section>
  );
}
