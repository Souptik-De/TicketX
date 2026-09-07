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
    throw new Error(problem.detail ?? "Request failed");
  }

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
