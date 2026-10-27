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

/**
 * Turn one FastAPI validation issue into readable text.
 *
 * A string detail is already written for humans. A list detail is Pydantic's
 * 422 shape: `{loc: ["body", "seat_layout", "tiers", 0, "rows", 1, "blocks"],
 * msg: "Input should be a valid integer"}`. The field path is what makes a
 * layout error actionable, so it is kept; "body" is noise and is dropped.
 */
function formatIssue(issue) {
  if (issue == null || typeof issue !== "object") {
    return String(issue ?? "");
  }
  const raw = typeof issue.msg === "string" ? issue.msg : "";
  // Model validators prefix their message with "Value error, "; the sentence
  // after it is the one the backend author wrote.
  const message = raw.replace(/^Value error,\s*/i, "");
  const path = Array.isArray(issue.loc)
    ? issue.loc.filter((part) => part !== "body" && part !== "query" && part !== "path").map(String).join(".")
    : "";
  if (message && path) return `${path}: ${message}`;
  return message || (path ? `${path}: invalid value` : "");
}

function problemMessage(problem, fallback) {
  if (typeof problem?.detail === "string" && problem.detail) {
    return problem.detail;
  }
  if (Array.isArray(problem?.detail)) {
    const parts = problem.detail.map(formatIssue).filter(Boolean);
    if (parts.length > 0) return parts.join("; ");
  }
  return fallback;
}

async function request(path, options) {
  const authHeaders = authToken ? { Authorization: `Bearer ${authToken}` } : {};
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...authHeaders, ...options?.headers },
  });

  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: "Request failed" }));
    const error = new Error(problemMessage(problem, "Request failed"));
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

/**
 * The fuzzy catalog search. All parameters are optional; the backend only
 * includes events that score above zero for the given query, so an empty query
 * with filters has to be sent as missing params rather than empty strings.
 */
export function searchEvents(params = {}) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.city) query.set("city", params.city);
  if (params.category) query.set("category", params.category);
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request(`/events/search${suffix}`);
}

export function updateEvent(eventId, payload) {
  return request(`/events/${eventId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/**
 * The seat map for one event. `signal` lets the seat page abort a poll that is
 * still in flight when the tab is hidden or the page is left.
 */
export function getEventSeats(eventId, options = {}) {
  return request(`/events/${eventId}/seats`, { signal: options.signal });
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

// --- Seat booking (used by the next part, declared here so it is the only
// place that knows these paths) -------------------------------------------

export function createHold(eventId, seatIds, options = {}) {
  return request(`/events/${eventId}/holds`, {
    method: "POST",
    body: JSON.stringify({ seat_ids: seatIds }),
    signal: options.signal,
  });
}

export function getHold(holdId, options = {}) {
  return request(`/holds/${holdId}`, { signal: options.signal });
}

export function confirmHold(holdId, body, options = {}) {
  return request(`/holds/${holdId}/confirm`, {
    method: "POST",
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function cancelHold(holdId, options = {}) {
  return request(`/holds/${holdId}`, { method: "DELETE", signal: options.signal });
}

export function joinEventWaitlist(eventId, body) {
  return request(`/events/${eventId}/waitlist`, {
    method: "POST",
    body: JSON.stringify(body),
  });
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

/** The caller's ticket history, revoked rows included. */
export function getMyTickets(options = {}) {
  return request("/tickets/mine", { signal: options.signal });
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
    throw new Error(problemMessage(problem, "Export failed"));
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


