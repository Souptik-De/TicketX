import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarDays, Compass, Home, LogOut, Moon, RadioTower, ScanLine, ShieldCheck, Sun, UserRound } from "lucide-react";

import { getEventSuggestions, getEvents, getGateStatus, getMyRegistrations, getVolunteers, leaveWaitlist, setAuthToken } from "./lib/api";
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
import { SuggestedEvents } from "./components/SuggestedEvents";
import { TicketIssuer } from "./components/TicketIssuer";
import { TicketPreview } from "./components/TicketPreview";
import { TicketXLogo } from "./components/TicketXLogo";

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
  const [suggestions, setSuggestions] = useState(null);
  const retryTimer = useRef(null);

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
  // events rather than a form that defaults to the first one.
  function handleLogin(user) {
    setSessionUser(user);
    setView(user.role === "user" ? "browse" : "workspace");
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
    } catch {
      // A transient failure should not blank out a position the user can see.
    }
  }, [sessionUser]);

  useEffect(() => {
    if (!sessionUser) {
      setMyRegistrations([]);
      setMyTickets([]);
      return undefined;
    }

    let isActive = true;
    const load = () => {
      getMyRegistrations()
        .then((data) => {
          if (isActive) {
            setMyRegistrations(data?.waitlist ?? []);
            setMyTickets(data?.tickets ?? []);
          }
        })
        .catch(() => undefined);
    };

    load();
    const timer = setInterval(load, WAITLIST_POLL_MS);

    return () => {
      isActive = false;
      clearInterval(timer);
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
      getEvents().then(setEvents).catch(() => undefined);
    }
    refreshMyRegistrations();
    // Registering changes what the recommendations are based on, so the row is
    // re-fetched rather than left showing events the attendee has just joined.
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
    setSuggestions(null);
  }

  function handleLogout() {
    clearSession();
    setSelectedEventId(null);
    setView("home");
  }

  function handleRoleAccess(role, eventId = "") {
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
        onGetTicket={(eventId) => handleRoleAccess("user", eventId)}
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
  const selectedEvent = chosenEvent ?? events[0];
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
                onClick={() => setView("browse")}
                aria-label="Browse events"
                title="Browse events"
              >
                <Compass size={18} aria-hidden="true" />
              </button>
            )}
            <button className="theme-toggle" type="button" onClick={() => setView("home")} aria-label="Return to events" title="Events">
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

      <OperationsSummary ticket={ticket} gates={allGateStatus} event={selectedEvent} />

      {workspace.id === "user" && view === "browse" && (
        <section className="user-browse">
          <SuggestedEvents
            suggestions={suggestions}
            myTickets={myTickets}
            selectedEventId={selectedEventId}
            onSelect={setSelectedEventId}
          />

          <EventBrowser
            events={events}
            isLoading={isLoadingEvents}
            error={loadError}
            myTickets={myTickets}
            selectedEventId={selectedEventId}
            onSelect={setSelectedEventId}
          />

          {chosenEvent && (
            <div className="workspace-grid user-grid browse-registration">
              <div className="left-stack">
                <TicketIssuer
                  events={events}
                  event={chosenEvent}
                  myTickets={myTickets}
                  sessionUser={sessionUser}
                  onEventChange={() => setSelectedEventId(null)}
                  onShowTicket={handleShowTicket}
                  onTicketIssued={handleTicketIssued}
                />
                <MyWaitlist
                  entries={myRegistrations}
                  onShowTicket={handleShowTicket}
                  onLeave={handleLeaveWaitlist}
                />
                <MyTickets tickets={myTickets} onShowTicket={handleShowTicket} />
              </div>
              <div className="right-stack">
                <TicketPreview ticket={ticket} />
              </div>
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
              onShowTicket={handleShowTicket}
              onTicketIssued={handleTicketIssued}
            />
            <MyWaitlist
              entries={myRegistrations}
              onShowTicket={handleShowTicket}
              onLeave={handleLeaveWaitlist}
            />
            <MyTickets tickets={myTickets} onShowTicket={handleShowTicket} />
          </div>
          <div className="right-stack">
            <TicketPreview ticket={ticket} />
          </div>
        </section>
      )}

      {workspace.id === "admin" && (
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

      {workspace.id === "gate" && (
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
