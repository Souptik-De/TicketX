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
- Allocate an individual General, Premium, or VIP seat number to each QR ticket.
- Download a complete PNG event pass containing the scannable QR, event name, ticket ID, attendee, tier, and seat.
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
| Admin | `POST /gates` | Add a gate to an event. |
| ET-01 | `POST /tickets` | Create attendee and issue ticket if event capacity remains. |
| ET-01 | `GET /tickets/{id}` | Return ticket details for the ticket owner/demo flow. |
| ET-02, ET-03 | `POST /scans` | Validate a ticket QR or ticket ID at a gate. |
| ET-04 | `GET /gates/status` | Return live scan counts and last synced time by gate. |

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the backend is running.
