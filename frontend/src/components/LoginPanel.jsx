import { useState } from "react";
import { LockKeyhole, ScanLine, ShieldCheck, UserRound } from "lucide-react";

import { login, setAuthToken } from "../lib/api";

const demoAccounts = [
  {
    role: "user",
    title: "User",
    username: "attendee",
    password: "attendee123",
    icon: UserRound,
    note: "Get tickets for available events.",
  },
  {
    role: "admin",
    title: "Admin",
    username: "admin",
    password: "admin123",
    icon: ShieldCheck,
    note: "Create events and manage gates.",
  },
  {
    role: "scanner",
    title: "Ticket Scanner",
    username: "scanner",
    password: "scanner123",
    icon: ScanLine,
    note: "Validate tickets at entry gates.",
  },
];

export function LoginPanel({ onLogin }) {
  const [username, setUsername] = useState("attendee");
  const [password, setPassword] = useState("attendee123");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      const session = await login({ username, password });
      setAuthToken(session.token);
      localStorage.setItem("ticketx-user", JSON.stringify(session.user));
      onLogin(session.user);
    } catch (err) {
      setAuthToken("");
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  function useDemoAccount(account) {
    setUsername(account.username);
    setPassword(account.password);
  }

  return (
    <main className="login-shell">
      <section className="login-hero">
        <p className="eyebrow">Secure Entry System</p>
        <h1>TicketX</h1>
        <p>Sign in with a role account to issue tickets, manage events, or scan QR codes at the gate.</p>
      </section>

      <section className="login-grid">
        <form className="panel login-card" onSubmit={handleSubmit}>
          <div className="section-heading">
            <LockKeyhole size={26} aria-hidden="true" />
            <h2>Login</h2>
          </div>
          <label>
            Username
            <input value={username} onChange={(event) => setUsername(event.target.value)} required />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          {error && <p className="error-text">{error}</p>}
          <button className="primary-button" disabled={isSubmitting} type="submit">
            <LockKeyhole size={18} aria-hidden="true" />
            {isSubmitting ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <div className="role-card-list">
          {demoAccounts.map((account) => {
            const Icon = account.icon;
            return (
              <button className="role-card" key={account.role} type="button" onClick={() => useDemoAccount(account)}>
                <Icon size={22} aria-hidden="true" />
                <span>
                  <strong>{account.title}</strong>
                  <small>
                    {account.username} / {account.password}
                  </small>
                  <em>{account.note}</em>
                </span>
              </button>
            );
          })}
        </div>
      </section>
    </main>
  );
}
