import { useState } from "react";
import { ArrowLeft, LockKeyhole, ScanLine, ShieldCheck, UserRound, UserPlus } from "lucide-react";

import { login, register, setAuthToken } from "../lib/api";

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

export function LoginPanel({ initialRole = "user", onBack, onLogin }) {
  const initialAccount = demoAccounts.find((account) => account.role === initialRole) ?? demoAccounts[0];
  const [username, setUsername] = useState(initialAccount.username);
  const [password, setPassword] = useState(initialAccount.password);
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      let session;
      if (isRegistering) {
        session = await register({ username, password, display_name: displayName, role: initialRole });
      } else {
        session = await login({ username, password });
      }
      setAuthToken(session.token);
      localStorage.setItem("ticketx-user", JSON.stringify(session.user));
      onLogin(session.user);
    } catch (err) {
      setAuthToken("");
      setError(err instanceof Error ? err.message : (isRegistering ? "Registration failed" : "Login failed"));
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
        <button className="login-back" type="button" onClick={onBack}>
          <ArrowLeft size={18} aria-hidden="true" />
          Back to events
        </button>
        <p className="eyebrow">Secure Entry System</p>
        <h1>TicketX</h1>
        <p>Sign in with a role account to issue tickets, manage events, or scan QR codes at the gate.</p>
      </section>

      <section className="login-grid">
        <form className="panel login-card" onSubmit={handleSubmit}>
          <div className="section-heading">
            {isRegistering ? <UserPlus size={26} aria-hidden="true" /> : <LockKeyhole size={26} aria-hidden="true" />}
            <h2>{isRegistering ? "Register" : "Login"}</h2>
          </div>
          {isRegistering && (
            <label>
              Display Name
              <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required />
            </label>
          )}
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
            {isRegistering ? <UserPlus size={18} aria-hidden="true" /> : <LockKeyhole size={18} aria-hidden="true" />}
            {isSubmitting ? (isRegistering ? "Registering..." : "Signing in...") : (isRegistering ? "Register" : "Sign in")}
          </button>
          <button type="button" onClick={() => setIsRegistering(!isRegistering)} style={{ background: 'none', border: 'none', cursor: 'pointer', marginTop: '1rem', color: 'inherit', textDecoration: 'underline' }}>
            {isRegistering ? "Already have an account? Login" : "Don't have an account? Register"}
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
