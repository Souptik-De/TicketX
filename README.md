# TicketX

TicketX is an event ticketing web application with QR-based gate validation. It has a user panel for attendees to get QR tickets, an admin panel for organizers to create events and monitor gates, and a volunteer scanner that rejects invalid or duplicate tickets.

## Stack

- Backend: FastAPI + SQLite + SQLAlchemy
- Frontend: React + Vite + JavaScript
- CI: GitHub Actions

## Run Locally

```powershell
python -m pip install -r backend/requirements.txt
npm install
npm run dev
```

The API runs at `http://127.0.0.1:8000`, and the React app runs at the URL printed by Vite, usually `http://127.0.0.1:5173`.

## Demo Accounts

| Role | Username | Password |
| --- | --- | --- |
| User | `attendee` | `attendee123` |
| Admin | `admin` | `admin123` |
| Ticket Scanner | `scanner` | `scanner123` |

Users can also sign in with Google once it is configured (see below). Google login is free and only needs an OAuth Client ID.

## Google Login Setup

1. Create an OAuth Client ID at `console.cloud.google.com` (APIs & Services > Credentials).
   Add your app origins, for example `http://127.0.0.1:5173` for local dev and your Vercel URL for production.
2. Backend: set `GOOGLE_CLIENT_ID` to that client ID before starting the API.
3. Frontend: set `VITE_GOOGLE_CLIENT_ID` to the same client ID before running `npm run dev` or building.
4. Restart both. The login screen will show "Continue with Google" for user and scanner roles. Admins keep using passwords.

If either variable is missing, Google login stays hidden and password login keeps working.

## Test

```powershell
npm test
```

This runs the FastAPI contract tests and verifies that the React app builds.

## Features

- Separate authenticated workspaces for attendees, admins, and ticket scanners.
- Password login plus optional Google login for users and scanners.
- Public event homepage with descriptions, dates, venues, capacity, and seating availability.
- Create events with descriptions from the admin panel.
- Add event-specific gates with assigned volunteers.
- Show issued and remaining seats with a per-tier breakdown for every event.
- Place a registrant on a visible waitlist when an event is full, and issue their ticket automatically when a seat is freed.
- Track your own waitlist position across reloads, and withdraw from a queue.
- Allocate an individual General, Premium, or VIP seat number to each QR ticket.
- Download a complete PNG event pass containing the scannable QR, event name, ticket ID, attendee, tier, and seat.
- Print or share a ticket, and fall back to a plain-text gate summary when a camera scan fails.
- Revoke a ticket from the admin panel, which releases the seat and frees it for the waitlist.
- Track per-event check-in charts and export attendance to CSV.
- Validate tickets by QR payload or camera scan.
- Reject reused tickets with duplicate-scan details.
- Monitor live check-in counts for each gate.
- Switch between light mode and night mode.

## API Contract

| Story | Endpoint | Purpose |
| --- | --- | --- |
| Auth | `POST /auth/login` | Sign in and receive a bearer token for the selected role. |
| Auth | `POST /auth/register` | Create a user or scanner account. |
| Auth | `POST /auth/google` | Sign in with a Google ID token (user and scanner roles). |
| Auth | `GET /auth/me` | Return the current signed-in user. |
| Public | `GET /events` | Return available events for the public homepage. |
| Admin | `POST /events` | Create a new event from the admin panel. |
| Admin | `DELETE /events/{id}` | Delete an event and everything attached to it. |
| Admin | `POST /gates` | Add a gate to an event. |
| ET-01 | `POST /tickets` | Create attendee and issue ticket if event capacity remains, otherwise join the waitlist. |
| ET-01 | `GET /tickets/{id}` | Return ticket details for the ticket owner/demo flow. |
| ET-02, ET-03 | `POST /scans` | Validate a ticket QR or ticket ID at a gate. |
| ET-04 | `GET /gates/status` | Return live scan counts and last synced time by gate. |
| ET-07 | `GET /me/registrations` | Return the caller's own waitlist places, with live position and any ticket issued by a promotion. |
| ET-07 | `DELETE /me/waitlist/{entry_id}` | Withdraw from a waitlist and close the gap in the queue. |
| ET-07 | `GET /events/{id}/waitlist` | Return an event's waiting attendees, admin only. |
| Admin | `GET /events/{id}/stats` | Return per-event attendance, check-in, waitlist, and recently promoted rows. |
| Admin | `GET /events/{id}/export` | Export the event attendance sheet as CSV. |
| ET-11 | `POST /tickets/{id}/revoke` | Revoke a ticket, releasing its seat. |

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the backend is running.

## Releasing a seat to the waitlist (ET-07 / ET-11)

A waitlist is only useful if a freed seat actually reaches the person at the front
of the line. That handover lives in one function so every path that frees a seat
gets it right:

```python
promote_next_waitlisted(db, event_id)  # backend/app/services.py
```

Any code that makes a seat available must follow this contract:

- **Call it once per seat freed.** It deliberately does not re-check capacity,
  because the caller has already decided the seat is free.
- **Call it inside the same transaction** as the change that freed the seat, and
  let the caller own the commit. A rollback then undoes the revocation and the
  promotion together, so a seat can never be lost or double-issued.
- **Loop for bulk revocations.** Revoking three tickets at once means three
  calls, not one.

`POST /tickets/{id}/revoke` follows this: it frees exactly one seat and promotes
exactly one person. It records the resulting `promoted_ticket_id` on the
waitlist entry, which is how the promoted attendee finds their ticket through
`GET /me/registrations` without guessing an identifier, and how an admin sees
the handover in `GET /events/{id}/stats`.

Note that revocation currently frees a seat even when the ticket was already
checked in, because the seat is released as soon as the ticket is revoked. That
is deliberate, but it is a policy choice rather than a technical necessity, so
change it in `revoke_ticket` if the event's rules say otherwise.

### Waiting is notified in-app

The promotion notice is in-app polling: the user workspace asks
`GET /me/registrations` every 15 seconds and shows a live position, plus a
"a ticket is ready for you" panel once a promotion lands. There is no email or
push channel yet. `promote_next_waitlisted` is the single place a promotion is
created, so adding one later means adding a send there, and `promoted_ticket_id`
already carries the recipient's ticket.

## Database

TicketX runs on SQLite, locally as `ticketx.db` and in production via
`DATABASE_URL`. There is no migration tool. `create_database()` calls
`Base.metadata.create_all()` (which only creates new tables) and then
`migrate_existing_database()`, which adds missing columns with hand-written
`ALTER TABLE` statements and backfills them.

Two consequences worth knowing:

- The migrator **only runs on SQLite** and logs a warning if `DATABASE_URL`
  points anywhere else. Pointing it at Postgres would leave existing tables
  unmigrated.
- Because production keeps a persistent SQLite file, `migrate_existing_database()`
  is a real upgrade path and not just a dev convenience. `backend/tests/test_contract.py`
  covers it against a database built with the pre-ET-07 schema; the rest of the
  suite uses `drop_all`/`create_all` and never exercises it.
