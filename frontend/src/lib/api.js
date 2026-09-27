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
    throw new Error(message);
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

export function getGates(eventId) {
  const query = eventId ? `?event_id=${eventId}` : "";
  return request(`/gates${query}`);
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

export function getEventWaitlist(eventId) {
  return request(`/events/${eventId}/waitlist`);
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


