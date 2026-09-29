const rawApiBase = import.meta.env.VITE_API_BASE_URL ?? "/api";
const API_BASE = rawApiBase.endsWith("/") ? rawApiBase.slice(0, -1) : rawApiBase;
let authToken = localStorage.getItem("ticketx-token") ?? "";

export function setAuthToken(token) {
  authToken = token;
  if (token) {
    localStorage.setItem("ticketx-token", token);
  } else {
    localStorage.removeItem("ticketx-token");
  }
}

async function request(path, options) {
  const authHeaders = authToken ? { Authorization: `Bearer ${authToken}` } : {};
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...authHeaders, ...options?.headers },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: "Request failed" }));
    let message = "Request failed";
    if (typeof problem.detail === "string") {
      message = problem.detail;
    } else if (Array.isArray(problem.detail)) {
      message = problem.detail.map((e) => e.msg || JSON.stringify(e)).join(", ");
    }
    const error = new Error(message);
    // Callers that need to branch on the failure -- a 409 from registering twice
    // for one event, say -- cannot tell the cases apart from the message alone.
    error.status = response.status;
    throw error;
  }

  if (response.status === 204) return null;
  return response.json();
}

export function login(payload) {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function register(payload) {
  return request("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function googleLogin(payload) {
  return request("/auth/google", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getEvents() {
  return request("/events");
}

export function createEvent(payload) {
  return request("/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteEvent(eventId) {
  return request(`/events/${eventId}`, {
    method: "DELETE",
  });
}

export function createGate(payload) {
  return request("/gates", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getVolunteers() {
  return request("/volunteers");
}

export function issueTicket(payload) {
  return request("/tickets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getTicket(ticketId) {
  return request(`/tickets/${ticketId}`);
}

export function scanTicket(payload) {
  return request("/scans", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getGateStatus(eventId) {
  const query = eventId ? `?event_id=${eventId}` : "";
  return request(`/gates/status${query}`);
}

export function getEventStats(eventId) {
  return request(`/events/${eventId}/stats`);
}

export function revokeTicket(ticketId) {
  return request(`/tickets/${ticketId}/revoke`, {
    method: "POST",
  });
}

/**
 * ET-07: the caller's own waitlist places, polled to show a live position and to
 * pick up a ticket that a promotion issued them.
 */
export function getMyRegistrations() {
  return request("/me/registrations");
}

export function getMyTicketStats() {
  return request("/me/ticket-stats");
}

export function getMyScans() {
  return request("/me/scans");
}

export function leaveWaitlist(entryId) {
  return request(`/me/waitlist/${entryId}`, {
    method: "DELETE",
  });
}

/**
 * Events recommended to the signed-in attendee, with a one-line reason each.
 *
 * The ranking is computed server-side without a model, so this is safe to call on
 * every visit to the browse view: a cold cache returns rule-based copy
 * immediately and never waits on the model.
 */
export function getEventSuggestions() {
  return request("/me/event-suggestions");
}

export async function exportAttendanceCsv(eventId) {
  const authHeaders = authToken ? { Authorization: `Bearer ${authToken}` } : {};
  const response = await fetch(`${API_BASE}/events/${eventId}/export`, {
    headers: { ...authHeaders },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: "Export failed" }));
    let message = "Export failed";
    if (typeof problem.detail === "string") {
      message = problem.detail;
    } else if (Array.isArray(problem.detail)) {
      message = problem.detail.map((e) => e.msg || JSON.stringify(e)).join(", ");
    }
    throw new Error(message);
  }

  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition");
  let filename = `event-${eventId}-attendance.csv`;
  if (disposition) {
    const match = disposition.match(/filename="?([^";]+)"?/);
    if (match?.[1]) {
      filename = match[1];
    }
  }

  return { blob, filename };
}


