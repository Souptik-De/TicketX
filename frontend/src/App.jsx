import { useEffect, useMemo, useState } from "react";
import { CalendarDays, LogOut, Moon, RadioTower, ShieldCheck, Sun, Ticket, UserRound } from "lucide-react";

import { getEvents, getGates, setAuthToken } from "./lib/api";
import { AdminPanel } from "./components/AdminPanel";
import { LoginPanel } from "./components/LoginPanel";

const panels = [
  { id: "user", label: "Ticket", icon: UserRound, roles: ["user", "admin"] },
  { id: "admin", label: "Admin", icon: ShieldCheck, roles: ["admin"] },
  { id: "gate", label: "Scanner", icon: Ticket, roles: ["scanner", "admin"] },
];

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
  const [activePanel, setActivePanel] = useState("user");
  const [theme, setTheme] = useState(() => localStorage.getItem("ticketx-theme") ?? "light");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [events, setEvents] = useState([]);
  const [gates, setGates] = useState([]);
  const [loadError, setLoadError] = useState("");

  const visiblePanels = useMemo(() => {
    if (!sessionUser) {
      return [];
    }
    return panels.filter((panel) => panel.roles.includes(sessionUser.role));
  }, [sessionUser]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("ticketx-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (sessionUser && visiblePanels.length > 0 && !visiblePanels.some((panel) => panel.id === activePanel)) {
      setActivePanel(visiblePanels[0].id);
    }
  }, [activePanel, sessionUser, visiblePanels]);

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

    const activeEventId = eventId || (eventList[0] ? eventList[0].id : "");
    if (!eventId && eventList[0]) {
      setSelectedEventId(eventList[0].id);
    }

    if (["admin", "scanner"].includes(sessionUser.role)) {
      const gateList = await getGates(activeEventId);
      setGates(gateList.map((gate) => ({ ...gate, scanned_count: gate.scanned_count ?? 0 })));
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
    setActivePanel(user.role === "scanner" ? "gate" : user.role === "admin" ? "admin" : "user");
  }

  function handleLogout() {
    setAuthToken("");
    localStorage.removeItem("ticketx-user");
    setSessionUser(null);
    setEvents([]);
    setGates([]);
    setSelectedEventId("");
  }

  if (!sessionUser) {
    return <LoginPanel onLogin={handleLogin} />;
  }

  const selectedEvent = events.find((event) => event.id === Number(selectedEventId)) ?? events[0];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <p className="eyebrow">Secure Entry System</p>
          <h1>TicketX</h1>
        </div>
        <div className="top-actions">
          {selectedEvent && (
            <div className="event-pill">
              <CalendarDays size={18} aria-hidden="true" />
              <span>{selectedEvent.title}</span>
            </div>
          )}
          <nav className="panel-tabs" aria-label="TicketX panels">
            {visiblePanels.map((panel) => {
              const Icon = panel.icon;
              return (
                <button
                  className={activePanel === panel.id ? "active" : ""}
                  key={panel.id}
                  type="button"
                  onClick={() => setActivePanel(panel.id)}
                >
                  <Icon size={18} aria-hidden="true" />
                  {panel.label}
                </button>
              );
            })}
          </nav>
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

      {activePanel === "user" && (
        <section className="workspace-grid user-grid">
          <div className="panel placeholder-panel">
            <div className="section-heading">
              <Ticket size={24} aria-hidden="true" />
              <h2>Ticket Panel</h2>
            </div>
            <p className="muted-text">Ticket issuance and attendee passes will be available here.</p>
          </div>
        </section>
      )}

      {activePanel === "admin" && (
        <AdminPanel
          events={events}
          gates={gates}
          selectedEventId={selectedEventId}
          onEventChange={setSelectedEventId}
          onEventCreated={(event) => {
            setEvents((current) => [...current, event].sort((left, right) => left.date_time.localeCompare(right.date_time)));
            setSelectedEventId(event.id);
          }}
          onGateCreated={() => refreshDirectory(selectedEventId)}
        />
      )}

      {activePanel === "gate" && (
        <section className="workspace-grid">
          <div className="panel placeholder-panel">
            <div className="section-heading">
              <Ticket size={24} aria-hidden="true" />
              <h2>Scanner Panel</h2>
            </div>
            <p className="muted-text">Ticket scanner and validation will be available here.</p>
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
