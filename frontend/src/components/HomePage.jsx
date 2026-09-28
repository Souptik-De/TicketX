import { ArrowRight, LogOut, Moon, ScanLine, ShieldCheck, Sun, Ticket, UserRound } from "lucide-react";

import { EventCard } from "./EventCard";
import { TicketXLogo } from "./TicketXLogo";
import { formatDate, remainingSeats } from "../lib/format";

const roles = [
  { id: "user", label: "User", shortLabel: "User", icon: UserRound },
  { id: "admin", label: "Admin", shortLabel: "Admin", icon: ShieldCheck },
  { id: "scanner", label: "Gate scanner", shortLabel: "Scanner", icon: ScanLine },
];

export function HomePage({ events, isLoading, error, sessionUser, theme, onThemeToggle, onRoleAccess, onLogout, onGetTicket }) {
  const featuredEvent = events[0];

  return (
    <main className="home-shell" id="top">
      <header className="home-nav">
        <div className="home-nav-bar">
          <a className="home-brand" href="#top" aria-label="TicketX home">
            <TicketXLogo size={28} />
            <strong>TicketX</strong>
          </a>
          <div className="home-nav-controls">
            <button className="theme-toggle" type="button" onClick={onThemeToggle} aria-label="Toggle night mode" title="Toggle theme">
              {theme === "light" ? <Moon size={18} aria-hidden="true" /> : <Sun size={18} aria-hidden="true" />}
            </button>
            {sessionUser && (
              <button className="theme-toggle" type="button" onClick={onLogout} aria-label="Sign out" title="Sign out">
                <LogOut size={18} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        <nav className="home-roles" aria-label="Account workspaces">
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
                <Icon size={16} aria-hidden="true" />
                <span className="role-label-full">{isCurrent ? `Open ${role.label}` : role.label}</span>
                <span className="role-label-short">{isCurrent ? `Open ${role.shortLabel}` : role.shortLabel}</span>
              </button>
            );
          })}
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
          {events.map((event) => (
            <EventCard
              key={event.id}
              event={event}
              actionIcon={Ticket}
              actionLabel={remainingSeats(event) === 0 ? "Join waitlist" : "Get ticket"}
              onSelect={onGetTicket}
            />
          ))}
        </div>
      </section>

      <footer className="home-footer">
        <strong>TicketX</strong>
        <span>Secure event access for every gate.</span>
      </footer>
    </main>
  );
}
