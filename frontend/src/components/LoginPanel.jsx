import { useState } from "react";
import { ArrowLeft, LockKeyhole, ScanLine, ShieldCheck, UserRound, UserPlus } from "lucide-react";

import { login, register, setAuthToken } from "../lib/api";

const roleAccounts = [
  {
    role: "user",
    title: "User",
    icon: UserRound,
    note: "Get tickets for available events.",
    color: "#0d8068",
  },
  {
    role: "admin",
    title: "Admin",
    icon: ShieldCheck,
    note: "Create events and manage gates.",
    color: "#6d28d9",
  },
  {
    role: "scanner",
    title: "Scanner",
    icon: ScanLine,
    note: "Validate tickets at entry gates.",
    color: "#d97706",
  },
];

export function LoginPanel({ initialRole = "user", onBack, onLogin }) {
  const [selectedRole, setSelectedRole] = useState(initialRole);
  const [isRegistering, setIsRegistering] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const currentRoleInfo = roleAccounts.find((r) => r.role === selectedRole) ?? roleAccounts[0];
  const RoleIcon = currentRoleInfo.icon;

  function handleRoleSwitch(role) {
    setSelectedRole(role);
    setError("");
    setUsername("");
    setPassword("");
    setDisplayName("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      let session;
      if (isRegistering) {
        session = await register({
          username,
          password,
          display_name: displayName || username,
          role: selectedRole,
        });
      } else {
        session = await login({ username, password });
      }
      setAuthToken(session.token);
      localStorage.setItem("ticketx-user", JSON.stringify(session.user));
      if (!isRegistering && session.user.role !== selectedRole) {
        setError(`This account has the "${session.user.role}" role, not "${selectedRole}". You'll enter the ${session.user.role} workspace.`);
        setTimeout(() => onLogin(session.user), 2000);
      } else {
        onLogin(session.user);
      }
    } catch (err) {
      setAuthToken("");
      setError(err instanceof Error ? err.message : isRegistering ? "Registration failed" : "Login failed");
    } finally {
      setIsSubmitting(false);
    }
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
        <p>Sign in or create an account to issue tickets, manage events, or scan QR codes at the gate.</p>
      </section>

      <section className="login-grid">
        {/* Role selector cards */}
        <div className="role-card-list">
          {roleAccounts.map((account) => {
            const Icon = account.icon;
            const isActive = selectedRole === account.role;
            return (
              <button
                className={`role-card${isActive ? " role-card-active" : ""}`}
                key={account.role}
                type="button"
                onClick={() => handleRoleSwitch(account.role)}
                style={isActive ? { borderColor: account.color, boxShadow: `0 0 0 2px ${account.color}33` } : {}}
              >
                <Icon size={22} aria-hidden="true" style={isActive ? { color: account.color } : {}} />
                <span>
                  <strong>{account.title}</strong>
                  <em>{account.note}</em>
                </span>
              </button>
            );
          })}
        </div>

        {/* Login / Register form */}
        <form className="panel login-card" onSubmit={handleSubmit}>
          <div className="section-heading" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <RoleIcon size={26} aria-hidden="true" style={{ color: currentRoleInfo.color }} />
            <div>
              <p className="eyebrow" style={{ margin: 0, fontSize: "0.72rem" }}>
                {currentRoleInfo.title} Account
              </p>
              <h2 style={{ margin: 0 }}>{isRegistering ? "Register" : "Login"}</h2>
            </div>
          </div>

          {/* Login / Register toggle tabs */}
          <div className="auth-tabs">
            <button
              type="button"
              className={`auth-tab${!isRegistering ? " auth-tab-active" : ""}`}
              onClick={() => { setIsRegistering(false); setError(""); }}
            >
              <LockKeyhole size={16} aria-hidden="true" />
              Login
            </button>
            <button
              type="button"
              className={`auth-tab${isRegistering ? " auth-tab-active" : ""}`}
              onClick={() => { setIsRegistering(true); setError(""); }}
            >
              <UserPlus size={16} aria-hidden="true" />
              Register
            </button>
          </div>

          {isRegistering && (
            <label>
              Display Name
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={`e.g. John Doe`}
                required
              />
            </label>
          )}
          <label>
            Username
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter your username"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              required
            />
          </label>
          {error && <p className="error-text">{error}</p>}
          <button
            className="primary-button"
            disabled={isSubmitting}
            type="submit"
            style={{ background: `linear-gradient(135deg, ${currentRoleInfo.color}, ${currentRoleInfo.color}cc)` }}
          >
            {isRegistering ? (
              <UserPlus size={18} aria-hidden="true" />
            ) : (
              <LockKeyhole size={18} aria-hidden="true" />
            )}
            {isSubmitting
              ? isRegistering
                ? "Registering..."
                : "Signing in..."
              : isRegistering
                ? `Register as ${currentRoleInfo.title}`
                : `Sign in as ${currentRoleInfo.title}`}
          </button>
        </form>
      </section>
    </main>
  );
}
