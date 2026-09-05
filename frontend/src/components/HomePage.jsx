import {
  ArrowRight,
  Armchair,
  CalendarDays,
  LogOut,
  MapPin,
  Moon,
  ScanLine,
  ShieldCheck,
  Sun,
  Ticket,
  UserRound,
} from "lucide-react";

const roles = [
  { id: "user", label: "User", icon: UserRound },
  { id: "admin", label: "Admin", icon: ShieldCheck },
  { id: "scanner", label: "Gate scanner", icon: ScanLine },
];

function formatDate(value) {
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function eventMonth(value) {
  return new Intl.DateTimeFormat("en-IN", { month: "short" }).format(new Date(value));
}

function eventDay(value) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit" }).format(new Date(value));
}

export function HomePage({ events, isLoading, error, sessionUser, theme, onThemeToggle, onRoleAccess, onLogout, onGetTicket }) {
  const featuredEvent = events[0];

  return (
    <main className="home-shell" id="top">
      <header className="home-nav">
        <a className="home-brand" href="#top" aria-label="TicketX home">
          <Ticket size={23} aria-hidden="true" />
          <strong>TicketX</strong>
        </a>
        <nav className="home-actions" aria-label="Account workspaces">
          {roles.map((role) => {
            const Icon = role.icon;
            const isCurrent = sessionUser?.role === role.id;
            return (
              <button
                className={`home-role-button${isCurrent ? " current" : ""}`}
                key={role.id}
                type="button"
                onClick={() => onRoleAccess(role.id)}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{isCurrent ? `Open ${role.label}` : role.label}</span>
              </button>
            );
          })}
          <button className="theme-toggle" type="button" onClick={onThemeToggle} aria-label="Toggle night mode">
            {theme === "light" ? <Moon size={18} aria-hidden="true" /> : <Sun size={18} aria-hidden="true" />}
          </button>
          {sessionUser && (
            <button className="theme-toggle" type="button" onClick={onLogout} aria-label="Sign out">
              <LogOut size={18} aria-hidden="true" />
            </button>
          )}
        </nav>
      </header>

      <section className="home-hero">
        <div className="home-hero-content">
          <p className="eyebrow">Campus events. One secure pass.</p>
          <h1>TicketX</h1>
          <p className="home-hero-copy">Discover what is happening next and reserve your individually numbered seat in minutes.</p>
          <div className="home-hero-actions">
            <a className="primary-button" href="#events">
              Browse events
              <ArrowRight size={18} aria-hidden="true" />
            </a>
            {featuredEvent && (
              <span className="next-event-line">
                Next: <strong>{featuredEvent.title}</strong> - {formatDate(featuredEvent.date_time)}
              </span>
            )}
          </div>
        </div>
      </section>

      <section className="event-catalog" id="events">
        <div className="catalog-heading">
          <div>
            <p className="eyebrow">Now booking</p>
            <h2>Upcoming events</h2>
          </div>
          <p>Choose an event to see availability, seating tiers, and reserve a QR ticket.</p>
        </div>

        {error && <div className="banner-error">{error}</div>}
        {isLoading && <div className="catalog-empty">Loading upcoming events...</div>}
        {!isLoading && !error && events.length === 0 && (
          <div className="catalog-empty">No events are accepting tickets right now.</div>
        )}

        <div className="event-card-grid">
          {events.map((event) => {
            const remaining = Math.max(event.capacity - event.issued_count, 0);
            const occupancy = event.capacity ? Math.min((event.issued_count / event.capacity) * 100, 100) : 0;
            const soldOut = remaining === 0;

            return (
              <article className="event-card" key={event.id}>
                <div className="event-card-topline">
                  <time className="event-date" dateTime={event.date_time}>
                    <strong>{eventDay(event.date_time)}</strong>
                    <span>{eventMonth(event.date_time)}</span>
                  </time>
                  <span className={`availability-tag${soldOut ? " sold-out" : ""}`}>
                    {soldOut ? "Sold out" : `${remaining} seats left`}
                  </span>
                </div>

                <div className="event-card-copy">
                  <h3>{event.title}</h3>
                  <p>{event.description || "More details about this event will be announced soon."}</p>
                </div>

                <div className="event-facts">
                  <span><CalendarDays size={17} aria-hidden="true" /> {formatDate(event.date_time)}</span>
                  <span><MapPin size={17} aria-hidden="true" /> {event.venue}</span>
                  <span><Armchair size={17} aria-hidden="true" /> {event.issued_count} of {event.capacity} issued</span>
                </div>

                <div className="capacity-meter" aria-label={`${Math.round(occupancy)} percent of seats issued`}>
                  <span style={{ width: `${occupancy}%` }} />
                </div>

                <div className="event-card-footer">
                  <div className="home-tier-list" aria-label="Issued seats by tier">
                    {["general", "premium", "vip"].map((tier) => {
                      const count = event.tier_counts?.find((item) => item.tier === tier)?.issued_count ?? 0;
                      return <span key={tier}><strong>{count}</strong> {tier}</span>;
                    })}
                  </div>
                  <button className="primary-button" type="button" disabled={soldOut} onClick={() => onGetTicket(event.id)}>
                    <Ticket size={18} aria-hidden="true" />
                    {soldOut ? "Unavailable" : "Get ticket"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <footer className="home-footer">
        <strong>TicketX</strong>
        <span>Secure event access for every gate.</span>
      </footer>
    </main>
  );
}
