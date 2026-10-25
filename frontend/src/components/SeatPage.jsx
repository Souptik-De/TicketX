import { useEffect, useRef, useState } from "react";
import { Armchair, ArrowLeft, CalendarDays, MapPin, RefreshCw } from "lucide-react";

import { getEventSeats } from "../lib/api";
import { formatDate } from "../lib/format";
import { SeatMap } from "./SeatMap";

// The map is shared, so it changes without anyone touching this page: someone
// else can hold or buy a seat at any moment. Five seconds is the brief's
// balance between "feels live" and "does not hammer the API".
const POLL_MS = 5000;

/**
 * Read-only seat map for one event.
 *
 * Polling rules worth keeping in mind when this grows a selection UI:
 * - one request at a time; a slow response never overlaps the next tick
 * - hidden tabs do not poll, and refresh exactly once when they return
 * - a transient network failure keeps the last good map on screen
 * - 404/409 are permanent answers, so polling stops
 * - 401 is handed to App, which signs the user out and remembers this event
 */
export function SeatPage({ event, isEventLoading = false, onBack, onUnauthorized }) {
  const [map, setMap] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [isReconnecting, setIsReconnecting] = useState(false);

  // The effect must not restart when App re-renders, so the callback is read
  // through a ref instead of being a dependency.
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const eventId = event?.id ?? null;

  useEffect(() => {
    if (!eventId) return undefined;

    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let controller = null;
    let lastPayload = "";

    setMap(null);
    setIsLoading(true);
    setError("");
    setIsReconnecting(false);

    async function load() {
      if (cancelled || stopped || inFlight || document.hidden) return;
      inFlight = true;
      controller = new AbortController();

      try {
        const data = await getEventSeats(eventId, { signal: controller.signal });
        if (cancelled) return;

        setIsReconnecting(false);
        setIsLoading(false);

        // Identical polls are dropped before React sees them: a 2000-seat map
        // re-rendering every five seconds for no reason is wasted work.
        const serialized = JSON.stringify(data);
        if (serialized !== lastPayload) {
          lastPayload = serialized;
          setMap(data);
        }
      } catch (requestError) {
        if (cancelled || requestError?.name === "AbortError") return;
        setIsLoading(false);

        if (requestError?.status === 401) {
          // Stop before the callback so a failed sign-out cannot loop.
          stopped = true;
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
    const timer = setInterval(load, POLL_MS);

    function handleVisibility() {
      if (!document.hidden) load();
    }
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibility);
      controller?.abort();
    };
  }, [eventId]);

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
    <section className="seat-page" aria-labelledby="seat-page-title">
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
          <button className="ghost-button" type="button" onClick={onBack}>
            <ArrowLeft size={16} aria-hidden="true" />
            Back
          </button>
        </div>

        <p className="seat-page-note">Seat booking is not available yet</p>

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

        {isLoading && !map && !error && <p className="muted-text">Loading the seat map...</p>}

        {map && <SeatMap tiers={map.tiers} readOnly />}
      </div>
    </section>
  );
}
