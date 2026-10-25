import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarDays, Compass, Home, LogOut, Moon, RadioTower, ScanLine, ShieldCheck, Sun, TicketCheck, UserRound } from "lucide-react";

import { getEventSuggestions, getEvents, getGateStatus, getMyRegistrations, getMyTickets, getVolunteers, leaveWaitlist, setAuthToken } from "./lib/api";
import { clearAllHoldIds } from "./lib/bookingStorage";
import { AdminPanel } from "./components/AdminPanel";
import { EventBrowser } from "./components/EventBrowser";
import { GateStatus } from "./components/GateStatus";
import { HomePage } from "./components/HomePage";
import { LoginPanel } from "./components/LoginPanel";
import { MyTickets } from "./components/MyTickets";
import { MyWaitlist } from "./components/MyWaitlist";
import { OperationsSummary } from "./components/OperationsSummary";
import { ScannerPanel } from "./components/ScannerPanel";
import { ScanResultCard } from "./components/ScanResultCard";
import { ScannerStats } from "./components/ScannerStats";
import { SeatPage } from "./components/SeatPage";
import { SuggestedEvents } from "./components/SuggestedEvents";
import { TicketIssuer } from "./components/TicketIssuer";
import { TicketPreview } from "./components/TicketPreview";
import { TicketXLogo } from "./components/TicketXLogo";
import { UserStats } from "./components/UserStats";

const workspaces = {
  user: { id: "user", label: "User workspace", icon: UserRound },
  admin: { id: "admin", label: "Admin workspace", icon: ShieldCheck },
  scanner: { id: "gate", label: "Scanner workspace", icon: ScanLine },
};

// How often to ask the server whether a seat has opened up. ET-07's in-app
// notice depends on this, since there is no push channel. The same poll carries
// "my tickets", so a promotion shows up in both places on the same tick.
const WAITLIST_POLL_MS = 15000;

// How long to wait before picking up model-written event reasons. The first
// response is rule-based while the copy is generated in the background, so this
// is a single, self-limiting retry rather than a poll.
const SUGGESTION_RETRY_MS = 5000;

function getStoredUser() {
  try {
    const storedUser = localStorage.getItem("ticketx-user");
    return storedUser ? JSON.parse(storedUser) : null;
  } catch {
    localStorage.removeItem("ticketx-user");
    return null;
  }
}

function App() {
  const [sessionUser, setSessionUser] = useState(getStoredUser);
  const [theme, setTheme] = useState(() => localStorage.getItem("ticketx-theme") ?? "light");
  const [view, setView] = useState("home");
  const [requestedRole, setRequestedRole] = useState("user");
  const [selectedEventId, setSelectedEventId] = useState(null);
  // The event whose seat page is open, and the one to return to after a
  // signed-out visitor signs in. Kept apart because a sign-in may be abandoned.
  const [seatEventId, setSeatEventId] = useState(null);
  const [pendingSeatEventId, setPendingSeatEventId] = useState(null);
  const registrationSectionRef = useRef(null);
  const [events, setEvents] = useState([]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(true);
  const [volunteers, setVolunteers] = useState([]);
  const [gateStatus, setGateStatus] = useState([]);
  const [allGateStatus, setAllGateStatus] = useState([]);
  const [ticket, setTicket] = useState(null);
  const [scanResult, setScanResult] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [myRegistrations, setMyRegistrations] = useState([]);
  const [myTickets, setMyTickets] = useState([]);
  const [ticketHistory, setTicketHistory] = useState([]);
  // Distinguishes "the server says this account has no queue place" from "the
  // first registrations response has not arrived yet". The seat page needs that
  // difference before it can trust localStorage over the server.
  const [isRegistrationsLoaded, setIsRegistrationsLoaded] = useState(false);
  const [suggestions, setSuggestions] = useState(null);
  const retryTimer = useRef(null);
  const checkoutGuardRef = useRef(null);
  const handleAuthExpiredRef = useRef(null);

  // The newest queue entry for the open seat event. `/me/registrations` is
  // already polled for the in-app promotion notice, so the seat page reads the
  // same server answer instead of trusting a position cached in localStorage.
  const seatWaitlistEntry = (() => {
    const eventId = Number(seatEventId);
    if (!eventId) return null;
    return myRegistrations.find((entry) => entry.event_id === eventId) ?? null;
  })();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("ticketx-theme", theme);
  }, [theme]);

  useEffect(() => {
    let isActive = true;
    setIsLoadingEvents(true);
    getEvents()
      .then((eventList) => {
        if (isActive) {
          setEvents(eventList);
          setLoadError("");
        }
      })
      .catch((error) => {
        if (isActive) {
          setLoadError(error instanceof Error ? error.message : "Could not load upcoming events");
        }
      })
      .finally(() => {
        if (isActive) {
          setIsLoadingEvents(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, []);

  async function refreshGateStatus(eventId = selectedEventId) {
    if (!["admin", "scanner"].includes(sessionUser?.role)) {
      return;
    }

    const [currentStatus, everyStatus] = await Promise.all([getGateStatus(eventId), getGateStatus()]);
    const normalize = (list) => list.map((item) => ({ ...item, id: item.gate_id ?? item.id }));
    setGateStatus(normalize(currentStatus));
    setAllGateStatus(normalize(everyStatus));
  }

  async function refreshDirectory(eventId = selectedEventId) {
    if (!sessionUser) {
      return;
    }

    const eventList = await getEvents();
    setEvents(eventList);

    // Gates and stats need an event to be pointed at, so those workspaces fall
    // back to the first one. An attendee does not: defaulting a selection here
    // would open the register form on an event they never picked, which is the
    // whole step this flow is trying to get them to choose deliberately.
    const needsAnEvent = ["admin", "scanner"].includes(sessionUser.role);
    if (needsAnEvent && !eventId && eventList[0]) {
      setSelectedEventId(eventList[0].id);
    }

    if (["admin", "scanner"].includes(sessionUser.role)) {
      const activeEventId = eventId || eventList[0]?.id || "";
      const [volunteerList, currentStatus, everyStatus] = await Promise.all([
        getVolunteers(),
        activeEventId ? getGateStatus(activeEventId) : [],
        getGateStatus(),
      ]);
      const normalize = (list) => list.map((item) => ({ ...item, id: item.gate_id ?? item.id }));
      setVolunteers(volunteerList);
      setGateStatus(normalize(currentStatus));
      setAllGateStatus(normalize(everyStatus));
    }
  }

  useEffect(() => {
    if (!sessionUser) {
      return;
    }

    refreshDirectory()
      .then(() => setLoadError(""))
      .catch((error) => {
        setLoadError(error instanceof Error ? error.message : "Could not load TicketX");
      });
  }, [sessionUser]);

  useEffect(() => {
    if (sessionUser && selectedEventId && ["admin", "scanner"].includes(sessionUser.role)) {
      refreshGateStatus(selectedEventId).catch((error) => {
        setLoadError(error instanceof Error ? error.message : "Could not refresh gates");
      });
    }
  }, [selectedEventId, sessionUser]);

  // An attendee lands on browsing, because registering is a choice between
  // events rather than a form that defaults to the first one. The exception is
  // a seat page they asked for before signing in: that is where they were
  // going, so that is where they land.
  function handleLogin(user) {
    setSessionUser(user);
    if (pendingSeatEventId) {
      setSeatEventId(pendingSeatEventId);
      setPendingSeatEventId(null);
      setView("seats");
      return;
    }
    setView(user.role === "user" ? "browse" : "workspace");
  }

  function openSeatPage(eventId) {
    const id = Number(eventId);
    if (!Number.isFinite(id)) return;

    if (sessionUser) {
      setSeatEventId(id);
      setPendingSeatEventId(null);
      setView("seats");
      return;
    }

    // Remember where they were headed, then use the app's normal sign-in step.
    setPendingSeatEventId(id);
    setRequestedRole("user");
    setView("login");
  }

  // The public homepage: a seat-map card opens the map, a legacy card keeps the
  // old "sign in, then register" path.
  function handleHomeEventSelect(eventId) {
    const target = events.find((event) => event.id === Number(eventId));
    if (target?.has_seat_map) {
      openSeatPage(target.id);
      return;
    }
    handleRoleAccess("user", eventId);
  }

  // The signed-in catalog: same split, without the login detour.
  function handleEventSelect(eventId) {
    const target = events.find((event) => event.id === Number(eventId));
    if (target?.has_seat_map) {
      openSeatPage(target.id);
      return;
    }
    setSelectedEventId(eventId);
    setTimeout(() => {
      registrationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }

  // A 401 on the seat page means the token is gone. Sign out, but keep the
  // event so the sign-in prompt can send them straight back.
  function handleSeatUnauthorized(eventId) {
    setPendingSeatEventId(eventId);
    clearSession();
    setRequestedRole("user");
    setView("login");
  }

  function closeSeatPage() {
    setSeatEventId(null);
    setView(sessionUser?.role === "user" ? "browse" : "workspace");
  }

  // ET-07: poll the caller's own queue places. This is what makes a position
  // survive a reload, what surfaces a promotion once ET-11's revocation has
  // issued the ticket, and what keeps "my tickets" and the queue in step.
  const refreshMyRegistrations = useCallback(async () => {
    if (!sessionUser) {
      return;
    }
    try {
      const data = await getMyRegistrations();
      setMyRegistrations(data?.waitlist ?? []);
      setMyTickets(data?.tickets ?? []);
      setIsRegistrationsLoaded(true);
    } catch {
      // A transient failure should not blank out a position the user can see.
      // Auth expiry is handled by the seat/ticket requests that need a live
      // session; this poll keeps the legacy workspace's silent-failure rule.
    }
  }, [sessionUser]);

  useEffect(() => {
    if (!sessionUser) {
      setMyRegistrations([]);
      setMyTickets([]);
      setIsRegistrationsLoaded(false);
      return undefined;
    }

    let isActive = true;
    setIsRegistrationsLoaded(false);
    const load = () => {
      getMyRegistrations()
        .then((data) => {
          if (isActive) {
            setMyRegistrations(data?.waitlist ?? []);
            setMyTickets(data?.tickets ?? []);
            setIsRegistrationsLoaded(true);
          }
        })
        .catch(() => {
          // Same rule as before Part 4: a failed background refresh leaves the
          // last known position on screen instead of signing the user out.
        });
    };

    load();
    const timer = setInterval(load, WAITLIST_POLL_MS);

    return () => {
      isActive = false;
      clearInterval(timer);
    };
  }, [sessionUser]);

  // Full ticket history, including revoked rows. It is separate from
  // `myTickets` above because EventBrowser uses that list to decide whether an
  // event is already booked, and a revoked ticket must not count as booked.
  // Same rules as the seat-map poll: user only, hidden tabs pause, one request
  // at a time, and the controller is aborted on unmount.
  useEffect(() => {
    if (!sessionUser || sessionUser.role !== "user") {
      setTicketHistory([]);
      return undefined;
    }

    let cancelled = false;
    let inFlight = false;
    let controller = null;

    async function load() {
      if (cancelled || inFlight || document.hidden) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const list = await getMyTickets({ signal: controller.signal });
        if (!cancelled) {
          setTicketHistory(list ?? []);
        }
      } catch (error) {
        if (error?.name === "AbortError") return;
        if (error?.status === 401) {
          handleAuthExpiredRef.current?.();
        }
      } finally {
        inFlight = false;
        controller = null;
      }
    }

    load();
    const timer = setInterval(load, WAITLIST_POLL_MS);
    function handleVisibility() {
      if (!document.hidden) load();
    }
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibility);
      controller?.abort();
    };
  }, [sessionUser]);

  // Recommendations are advisory, so a failure here must never disturb the
  // catalog below it. Silently leaving the row out is the whole error strategy.
  const refreshSuggestions = useCallback(async ({ allowRetry = true } = {}) => {
    if (!sessionUser || sessionUser.role !== "user") {
      return;
    }
    try {
      const data = await getEventSuggestions();
      setSuggestions(data);

      // The first response is rule-based copy while the server generates the real
      // wording in the background, so this is when the attendee would otherwise be
      // looking at the boring version. One refetch picks up the generated copy
      // without making them reload the page.
      //
      // Only the first load arms this. The retry passes allowRetry: false, so an
      // unreachable or unconfigured model cannot turn into an endless poll.
      const isStillRuleBased = (data?.items?.length ?? 0) > 0 && data.items.every((item) => item.source !== "ai");
      if (allowRetry && data?.ai_enabled && isStillRuleBased) {
        clearTimeout(retryTimer.current);
        retryTimer.current = setTimeout(() => refreshSuggestions({ allowRetry: false }), SUGGESTION_RETRY_MS);
      }
    } catch {
      // Left as-is: the row is supplementary.
    }
  }, [sessionUser]);

  useEffect(() => {
    if (!sessionUser) {
      setSuggestions(null);
      return undefined;
    }
    refreshSuggestions();
    // Cancelled on unmount and on sign-out so a pending retry cannot land in a
    // different session.
    return () => clearTimeout(retryTimer.current);
  }, [refreshSuggestions]);

  async function handleLeaveWaitlist(entryId) {
    await leaveWaitlist(entryId);
    await refreshMyRegistrations();
  }

  function handleShowTicket(chosenTicket) {
    setTicket(chosenTicket);
  }

  function handleTicketIssued(issuedTicket) {
    setTicket(issuedTicket);
    // Only a real issuance changes the seat counts. A waitlist join moves the
    // card to "Join waitlist" only if the event was already full, which it is by
    // definition at that point, so there is nothing to redraw.
    if (issuedTicket.outcome !== "waitlisted") {
      // TicketIssuer hands over the full detail, so the ticket history can
      // update on this tick instead of waiting for its poll. The legacy
      // workspace's own list is refreshed separately from /me/registrations.
      setTicketHistory((current) => [
        issuedTicket,
        ...current.filter((item) => item.ticket_id !== issuedTicket.ticket_id),
      ]);
      getEvents().then(setEvents).catch(() => undefined);
    }
    refreshMyRegistrations();
    // Registering changes what the recommendations are based on, so the row is
    // re-fetched rather than left showing events the attendee has just joined.
    refreshSuggestions({ allowRetry: false });
  }

  function handleBooked(confirmedTickets) {
    if (Array.isArray(confirmedTickets) && confirmedTickets.length > 0) {
      // Show the new passes immediately; the 15-second history poll will replace
      // this optimistic merge with the server's list.
      setTicketHistory((current) => {
        const ids = new Set(confirmedTickets.map((item) => item.ticket_id));
        return [...confirmedTickets, ...current.filter((item) => !ids.has(item.ticket_id))];
      });
    }
    getEvents().then(setEvents).catch(() => undefined);
    refreshMyRegistrations();
    refreshSuggestions({ allowRetry: false });
  }

  function clearSession() {
    setAuthToken("");
    localStorage.removeItem("ticketx-user");
    setSessionUser(null);
    setTicket(null);
    setScanResult(null);
    setVolunteers([]);
    setGateStatus([]);
    setAllGateStatus([]);
    setMyRegistrations([]);
    setMyTickets([]);
    setTicketHistory([]);
    setIsRegistrationsLoaded(false);
    setSuggestions(null);
  }

  // A 401 from a background poll has no seat page to hand it to. Sign out, but
  // deliberately do not clear the stored hold ids: the same account can resume
  // after signing back in.
  function handleAuthExpired() {
    clearSession();
    setRequestedRole("user");
    setView("login");
  }
  handleAuthExpiredRef.current = handleAuthExpired;

  async function runCheckoutGuard() {
    const guard = checkoutGuardRef.current;
    if (!guard) return true;
    try {
      const result = await guard();
      return result !== false;
    } catch {
      // A broken guard must never trap the attendee.
      return true;
    }
  }

  // Any in-app navigation while checkout is open counts as Cancel. The guard is
  // capped inside SeatPage, so this await cannot hold the UI hostage.
  function navigate(action) {
    runCheckoutGuard().then((proceed) => {
      if (proceed) action();
    });
  }

  // The top-bar destinations are all outside the seat page. Clearing the seat
  // event here keeps the next visit from inheriting a stale "seats" context.
  function navigateAwayFromSeats(action) {
    navigate(() => {
      setSeatEventId(null);
      action();
    });
  }

  async function handleLogout() {
    const proceed = await runCheckoutGuard();
    if (!proceed) return;

    // Explicit sign-out discards this account's stored hold ids. A 401 does not.
    clearAllHoldIds(sessionUser?.id);
    clearSession();
    setSelectedEventId(null);
    setSeatEventId(null);
    setPendingSeatEventId(null);
    setView("home");
  }

  function goBrowse() {
    setView("browse");
    window.setTimeout(() => {
      document.getElementById("events")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }

  function handleRoleAccess(role, eventId = "") {
    // Picking a workspace from the sign-in screen ends any seat-page detour;
    // otherwise a later sign-in would reopen a map the visitor left behind.
    setPendingSeatEventId(null);

    if (eventId) {
      setSelectedEventId(eventId);
    }

    if (sessionUser?.role === role) {
      // An attendee goes back to browsing rather than to a form that already has
      // an event in it, so the choice stays theirs.
      setView(role === "user" ? "browse" : "workspace");
      return;
    }

    if (sessionUser) {
      clearSession();
    }
    setRequestedRole(role);
    setView("login");
  }

  if (view === "home") {
    return (
      <HomePage
        events={events}
        isLoading={isLoadingEvents}
        error={loadError}
        sessionUser={sessionUser}
        theme={theme}
        onThemeToggle={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
        onRoleAccess={handleRoleAccess}
        onLogout={handleLogout}
        onGetTicket={handleHomeEventSelect}
      />
    );
  }

  if (!sessionUser) {
    return <LoginPanel initialRole={requestedRole} onBack={() => setView("home")} onLogin={handleLogin} />;
  }

  // The admin, scanner, and legacy user workspaces all need *an* event to be
  // selected, so this one falls back to the first. Browsing must not, or the
  // register form would open on an event nobody picked.
  const chosenEvent = events.find((event) => event.id === Number(selectedEventId)) ?? null;
  // The seat page has its own event id; without this the top bar would keep
  // showing whichever event the browse view last selected.
  const seatEvent = events.find((event) => event.id === Number(seatEventId)) ?? null;
  // The ticket history spans events, so it has no single event to show in the
  // top bar or the operations summary.
  const selectedEvent = view === "seats" ? seatEvent : view === "tickets" ? null : chosenEvent ?? events[0];
  const workspace = workspaces[sessionUser.role] ?? workspaces.user;
  const WorkspaceIcon = workspace.icon;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-main">
          <div className="brand-block">
            <p className="eyebrow">Secure Entry System</p>
            <h1 className="brand-title">
              <TicketXLogo size={28} />
              TicketX
            </h1>
          </div>
          <div className="top-action-buttons">
            {workspace.id === "user" && (
              <button
                className="theme-toggle"
                type="button"
                onClick={() => navigateAwayFromSeats(() => setView("browse"))}
                aria-label="Browse events"
                title="Browse events"
              >
                <Compass size={18} aria-hidden="true" />
              </button>
            )}
            {workspace.id === "user" && (
              <button
                className="theme-toggle"
                type="button"
                onClick={() => navigateAwayFromSeats(() => setView("tickets"))}
                aria-label="My tickets"
                title="My tickets"
              >
                <TicketCheck size={18} aria-hidden="true" />
              </button>
            )}
            <button className="theme-toggle" type="button" onClick={() => navigateAwayFromSeats(() => setView("home"))} aria-label="Return to events" title="Events">
              <Home size={18} aria-hidden="true" />
            </button>
            <button
              className="theme-toggle"
              type="button"
              onClick={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
              aria-label="Toggle night mode"
              title="Toggle theme"
            >
              {theme === "light" ? <Moon size={18} aria-hidden="true" /> : <Sun size={18} aria-hidden="true" />}
            </button>
            <button className="theme-toggle" type="button" onClick={handleLogout} aria-label="Sign out" title="Sign out">
              <LogOut size={18} aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="topbar-pills">
          {selectedEvent && (
            <div className="event-pill">
              <CalendarDays size={16} aria-hidden="true" />
              <span>{selectedEvent.title}</span>
            </div>
          )}
          <div className="workspace-badge">
            <WorkspaceIcon size={16} aria-hidden="true" />
            <span>{workspace.label}</span>
          </div>
        </div>
      </header>

      <div className="session-strip">
        <span>{sessionUser.display_name}</span>
        <strong>{sessionUser.role}</strong>
      </div>

      {loadError && <div className="banner-error">{loadError}</div>}

      {view !== "seats" && view !== "tickets" && (
        <OperationsSummary ticket={ticket} gates={allGateStatus} event={selectedEvent} />
      )}

      {view === "seats" && (
        <SeatPage
          event={events.find((event) => event.id === Number(seatEventId)) ?? null}
          isEventLoading={isLoadingEvents}
          sessionUser={sessionUser}
          onBack={closeSeatPage}
          onUnauthorized={handleSeatUnauthorized}
          onViewTickets={() => {
            setSeatEventId(null);
            setView("tickets");
          }}
          onBooked={handleBooked}
          onCheckoutGuardChange={(guard) => {
            checkoutGuardRef.current = guard;
          }}
          onWaitlistJoined={refreshMyRegistrations}
          liveWaitlistEntry={seatWaitlistEntry}
          isWaitlistLoaded={isRegistrationsLoaded}
        />
      )}

      {workspace.id === "user" && view === "browse" && (
        <section className="user-browse">
          <SuggestedEvents
            suggestions={suggestions}
            myTickets={myTickets}
            selectedEventId={selectedEventId}
            onSelect={handleEventSelect}
          />

          <EventBrowser
            events={events}
            isLoading={isLoadingEvents}
            error={loadError}
            myTickets={myTickets}
            selectedEventId={selectedEventId}
            onSelect={handleEventSelect}
          />

          <UserStats refreshKey={myTickets.length} />

          {chosenEvent && (
            <div className="workspace-grid user-grid browse-registration" ref={registrationSectionRef}>
              <div className="left-stack">
                <TicketIssuer
                  events={events}
                  event={chosenEvent}
                  myTickets={myTickets}
                  sessionUser={sessionUser}
                  onEventChange={() => setSelectedEventId(null)}
                  onChooseSeats={openSeatPage}
                  onShowTicket={handleShowTicket}
                  onTicketIssued={handleTicketIssued}
                />
                <MyWaitlist
                  entries={myRegistrations}
                  onShowTicket={handleShowTicket}
                  onLeave={handleLeaveWaitlist}
                />
                <MyTickets tickets={myTickets} onShowTicket={handleShowTicket} onBrowse={goBrowse} />
              </div>
              <div className="right-stack">
                <TicketPreview ticket={ticket} />
              </div>
            </div>
          )}
        </section>
      )}

      {workspace.id === "user" && view === "tickets" && (
        <section className={ticket ? "workspace-grid user-grid" : "workspace-grid user-tickets-single"}>
          <div className="left-stack">
            <MyTickets tickets={ticketHistory} onShowTicket={handleShowTicket} onBrowse={goBrowse} showEmpty />
          </div>
          {ticket && (
            <div className="right-stack">
              <TicketPreview ticket={ticket} />
            </div>
          )}
        </section>
      )}

      {workspace.id === "user" && view === "workspace" && (
        <section className="workspace-grid user-grid">
          <div className="left-stack">
            <TicketIssuer
              events={events}
              initialEventId={selectedEventId}
              myTickets={myTickets}
              sessionUser={sessionUser}
              onEventChange={() => setView("browse")}
              onChooseSeats={openSeatPage}
              onShowTicket={handleShowTicket}
              onTicketIssued={handleTicketIssued}
            />
            <MyWaitlist
              entries={myRegistrations}
              onShowTicket={handleShowTicket}
              onLeave={handleLeaveWaitlist}
            />
            <UserStats refreshKey={myTickets.length} />
            <MyTickets tickets={myTickets} onShowTicket={handleShowTicket} onBrowse={goBrowse} />
          </div>
          <div className="right-stack">
            <TicketPreview ticket={ticket} />
          </div>
        </section>
      )}

      {workspace.id === "admin" && view !== "seats" && (
        <AdminPanel
          events={events}
          gates={gateStatus}
          selectedEventId={selectedEventId}
          onEventChange={setSelectedEventId}
          onEventCreated={(event) => {
            setEvents((current) => [...current, event].sort((left, right) => left.date_time.localeCompare(right.date_time)));
            setSelectedEventId(event.id);
          }}
          onGateCreated={() => refreshDirectory()}
          onEventDeleted={(eventId) => {
            setEvents((current) => current.filter((e) => e.id !== eventId));
            if (selectedEventId === eventId) setSelectedEventId(null);
          }}
        />
      )}

      {workspace.id === "gate" && view !== "seats" && (
        <section className="workspace-grid">
          <div className="left-stack">
            <ScannerPanel
              events={events}
              selectedEventId={selectedEventId}
              onEventChange={setSelectedEventId}
              gates={gateStatus}
              volunteers={volunteers}
              onScanComplete={setScanResult}
              onGateRefresh={() => refreshGateStatus()}
            />
          </div>
          <div className="right-stack">
            <ScanResultCard result={scanResult} />
            <ScannerStats refreshSignal={scanResult} />
            <GateStatus gates={gateStatus} onRefresh={() => refreshGateStatus()} />
          </div>
        </section>
      )}

      <footer className="footer-note">
        <RadioTower size={16} aria-hidden="true" />
        <span>Gate sync active</span>
      </footer>
    </main>
  );
}

export default App;
