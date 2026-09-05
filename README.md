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

## Test

```powershell
npm test
```

This runs the FastAPI contract tests and verifies that the React app builds.

## Features

- Separate authenticated workspaces for attendees, admins, and ticket scanners.
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
| Auth | `GET /auth/me` | Return the current signed-in user. |
| Public | `GET /events` | Return available events for the public homepage. |
| Admin | `POST /events` | Create a new event from the admin panel. |
| Admin | `POST /gates` | Add a gate to an event. |
| ET-01 | `POST /tickets` | Create attendee and issue ticket if event capacity remains. |
| ET-01 | `GET /tickets/{id}` | Return ticket details for the ticket owner/demo flow. |
| ET-02, ET-03 | `POST /scans` | Validate a ticket QR or ticket ID at a gate. |
| ET-04 | `GET /gates/status` | Return live scan counts and last synced time by gate. |

Interactive API docs are available at `http://127.0.0.1:8000/docs` while the backend is running.
