import { useState } from "react";
import { ArrowLeft, LockKeyhole, ScanLine, ShieldCheck, UserRound, UserPlus } from "lucide-react";
import { GoogleLogin, GoogleOAuthProvider } from "@react-oauth/google";

import { googleLogin, login, register, setAuthToken } from "../lib/api";

const roleAccounts = [
  {
    role: "user",
    title: "User",
    icon: UserRound,
    note: "Get tickets for available events.",
  },
  {
    role: "admin",
    title: "Admin",
    icon: ShieldCheck,
    note: "Manage events and gates.",
  },
  {
    role: "scanner",
    title: "Scanner",
    icon: ScanLine,
    note: "Validate tickets at entry gates.",
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
  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";

  const currentRoleInfo = roleAccounts.find((r) => r.role === selectedRole) ?? roleAccounts[0];
  const RoleIcon = currentRoleInfo.icon;

  function handleRoleSwitch(role) {
    setSelectedRole(role);
    if (role === "admin") {
      setIsRegistering(false);
    }
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
        if (selectedRole === "admin") {
          setError("Admin registration is not available. Please sign in with administrator credentials.");
          setIsSubmitting(false);
          return;
        }
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

  function handleSession(session) {
    setAuthToken(session.token);
    localStorage.setItem("ticketx-user", JSON.stringify(session.user));
    if (session.user.role !== selectedRole) {
      setError(`This account has the "${session.user.role}" role, not "${selectedRole}". You'll enter the ${session.user.role} workspace.`);
      setTimeout(() => onLogin(session.user), 2000);
    } else {
      onLogin(session.user);
    }
  }

  async function handleGoogleSuccess(credentialResponse) {
    const credential = credentialResponse?.credential;
    if (!credential) {
      setError("Google login did not return an account. Please try again.");
      return;
    }
    setError("");
    setIsSubmitting(true);
    try {
      const session = await googleLogin({ id_token: credential, role: selectedRole });
      handleSession(session);
    } catch (err) {
      setAuthToken("");
      setError(err instanceof Error ? err.message : "Google login failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleGoogleError() {
    setError("Google login was cancelled or failed. Please try again.");
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
              >
                <Icon size={22} aria-hidden="true" />
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
          <div className="section-heading login-title">
            <RoleIcon size={26} aria-hidden="true" />
            <div>
              <p className="eyebrow">
                {currentRoleInfo.title} Account
              </p>
              <h2>{isRegistering ? "Register" : "Login"}</h2>
            </div>
          </div>

          {/* Login / Register toggle tabs - only for non-admin roles */}
          {selectedRole !== "admin" && (
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
          )}

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
          {selectedRole !== "admin" && googleClientId && (
            <>
              <div className="google-divider">
                <span>or</span>
              </div>
              <div className="google-wrap">
                <GoogleOAuthProvider clientId={googleClientId}>
                  <GoogleLogin
                    onSuccess={handleGoogleSuccess}
                    onError={handleGoogleError}
                    text="continue_with"
                    shape="rectangular"
                    size="large"
                    width="100%"
                  />
                </GoogleOAuthProvider>
              </div>
            </>
          )}
        </form>
      </section>
    </main>
  );
}
