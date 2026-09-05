import { useEffect, useState } from "react";
import { CalendarDays, Home, LogOut, Moon, RadioTower, ScanLine, ShieldCheck, Sun, UserRound } from "lucide-react";

import { getEvents, getGates, getVolunteers, setAuthToken } from "./lib/api";
import { AdminPanel } from "./components/AdminPanel";
import { HomePage } from "./components/HomePage";
import { LoginPanel } from "./components/LoginPanel";
import { ScannerPanel } from "./components/ScannerPanel";
import { ScanResultCard } from "./components/ScanResultCard";
import { TicketIssuer } from "./components/TicketIssuer";
import { TicketPreview } from "./components/TicketPreview";

const workspaces = {
  user: { id: "user", label: "User workspace", icon: UserRound },
  admin: { id: "admin", label: "Admin workspace", icon: ShieldCheck },
  scanner: { id: "gate", label: "Scanner workspace", icon: ScanLine },
};

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
  const [selectedEventId, setSelectedEventId] = useState("");
  const [events, setEvents] = useState([]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(true);
  const [gates, setGates] = useState([]);
  const [volunteers, setVolunteers] = useState([]);
  const [ticket, setTicket] = useState(null);
  const [scanResult, setScanResult] = useState(null);
  const [loadError, setLoadError] = useState("");

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

  async function refreshGates(eventId = selectedEventId) {
    if (!["admin", "scanner"].includes(sessionUser?.role)) {
      return;
    }

    const gateList = await getGates(eventId);
    setGates(gateList.map((gate) => ({ ...gate, scanned_count: gate.scanned_count ?? 0 })));
  }

  async function refreshDirectory(eventId = selectedEventId) {
    if (!sessionUser) {
      return;
    }

    const eventList = await getEvents();
    setEvents(eventList);

    if (!eventId && eventList[0]) {
      setSelectedEventId(eventList[0].id);
    }

    if (["admin", "scanner"].includes(sessionUser.role)) {
      const activeEventId = eventId || eventList[0]?.id || "";
      if (activeEventId) {
        const [gateList, volunteerList] = await Promise.all([getGates(activeEventId), getVolunteers()]);
        setGates(gateList.map((gate) => ({ ...gate, scanned_count: gate.scanned_count ?? 0 })));
        setVolunteers(volunteerList);
      }
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
      refreshGates(selectedEventId).catch((error) => {
        setLoadError(error instanceof Error ? error.message : "Could not refresh gates");
      });
    }
  }, [selectedEventId, sessionUser]);

  function handleLogin(user) {
    setSessionUser(user);
    setView("workspace");
  }

  function handleTicketIssued(issuedTicket) {
    setTicket(issuedTicket);
    getEvents().then(setEvents).catch(() => undefined);
  }

  function clearSession() {
    setAuthToken("");
    localStorage.removeItem("ticketx-user");
    setSessionUser(null);
    setTicket(null);
    setScanResult(null);
    setGates([]);
    setVolunteers([]);
  }

  function handleLogout() {
    clearSession();
    setSelectedEventId("");
    setView("home");
  }

  function handleRoleAccess(role, eventId = "") {
    if (eventId) {
      setSelectedEventId(eventId);
    }

    if (sessionUser?.role === role) {
      setView("workspace");
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

  const selectedEvent = events.find((event) => event.id === Number(selectedEventId)) ?? events[0];
  const workspace = workspaces[sessionUser.role] ?? workspaces.user;
  const WorkspaceIcon = workspace.icon;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <p className="eyebrow">Secure Entry System</p>
          <h1>TicketX</h1>
        </div>
        <div className="top-actions">
          <button className="theme-toggle" type="button" onClick={() => setView("home")} aria-label="Return to events">
            <Home size={18} aria-hidden="true" />
          </button>
          {selectedEvent && (
            <div className="event-pill">
              <CalendarDays size={18} aria-hidden="true" />
              <span>{selectedEvent.title}</span>
            </div>
          )}
          <div className="workspace-badge">
            <WorkspaceIcon size={18} aria-hidden="true" />
            <span>{workspace.label}</span>
          </div>
          <button
            className="theme-toggle"
            type="button"
            onClick={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
            aria-label="Toggle night mode"
          >
            {theme === "light" ? <Moon size={18} aria-hidden="true" /> : <Sun size={18} aria-hidden="true" />}
          </button>
          <button className="theme-toggle" type="button" onClick={handleLogout} aria-label="Sign out">
            <LogOut size={18} aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="session-strip">
        <span>{sessionUser.display_name}</span>
        <strong>{sessionUser.role}</strong>
      </div>

      {loadError && <div className="banner-error">{loadError}</div>}

      {workspace.id === "user" && (
        <section className="workspace-grid user-grid">
          <div className="left-stack">
            <TicketIssuer events={events} initialEventId={selectedEventId} onTicketIssued={handleTicketIssued} />
          </div>
          <div className="right-stack">
            <TicketPreview ticket={ticket} />
          </div>
        </section>
      )}

      {workspace.id === "admin" && (
        <AdminPanel
          events={events}
          gates={gates}
          selectedEventId={selectedEventId}
          onEventChange={setSelectedEventId}
          onEventCreated={(event) => {
            setEvents((current) => [...current, event].sort((left, right) => left.date_time.localeCompare(right.date_time)));
            setSelectedEventId(event.id);
          }}
          onGateCreated={() => refreshDirectory()}
        />
      )}

      {workspace.id === "gate" && (
        <section className="workspace-grid">
          <div className="left-stack">
            <ScannerPanel
              gates={gates}
              volunteers={volunteers}
              onScanComplete={setScanResult}
              onGateRefresh={() => refreshGates()}
            />
          </div>
          <div className="right-stack">
            <ScanResultCard result={scanResult} />
          </div>
        </section>
      )}

      <footer className="footer-note">
        <RadioTower size={16} aria-hidden="true" />
        <span>Scanner validation active</span>
      </footer>
    </main>
  );
}

export default App;
