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

## Configuration

`backend/.env.example` and `frontend/.env.example` list every variable with what
it does. Copy them to `backend/.env` and `frontend/.env` for local work. Both
`.env` files are gitignored; `backend/app/config.py` loads `backend/.env` on
startup with `load_dotenv()`, which does **not** override variables that are
already set, so your shell and your host's settings always win.

| Variable | Where | Effect if unset |
| --- | --- | --- |
| `DATABASE_URL` | backend | Falls back to `sqlite:///./ticketx.db` |
| `TICKETX_SECRET`, `TICKETX_PASSWORD_SALT` | backend | Dev defaults. **Change both before exposing the app.** |
| `GOOGLE_CLIENT_ID` | backend | Password login only; the Google button stays hidden |
| `GROQ_API_KEY` | backend | Event suggestions still work, with rule-based copy instead of model-written copy |
| `GROQ_MODEL` | backend | Defaults to `openai/gpt-oss-120b` |
| `TICKETX_SEED_DEMO` | backend | Demo events and registrations are not created. See [Demo data](#demo-data) |
| `VITE_GOOGLE_CLIENT_ID` | frontend | Same as `GOOGLE_CLIENT_ID`, from the browser's side |
| `VITE_API_BASE_URL` | frontend | Frontend calls `/api`, which the Vite dev server proxies to `127.0.0.1:8000` |

> **Deploying the frontend somewhere other than localhost?** Set
> `VITE_API_BASE_URL` to the absolute API origin. Leave it unset and the app
> requests `/api/events`, which on Vercel is caught by the `rewrites` rule in
> `frontend/vercel.json` and answered with `index.html` — the UI then shows
> `Unexpected token '<'`. Vite inlines this at **build** time, so it needs a
> rebuild, not just a restart.

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
- Browse events after signing in, then register for the one you picked, with your name and email prefilled from your account.
- A "Recommended for you" row above the catalog, ranked from your own registrations and what other attendees are booking, with a one-line reason for each.
- One seat per person per event: registering again for the same event is refused instead of quietly taking another seat.
- See every ticket you hold after a reload, and reopen its QR pass from the list.
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
| User | `GET /me/event-suggestions` | Rank registrable events for the caller and give a one-line reason for each. |
| Admin | `POST /events` | Create a new event from the admin panel. |
| Admin | `DELETE /events/{id}` | Delete an event and everything attached to it. |
| Admin | `POST /gates` | Add a gate to an event. |
| ET-01 | `POST /tickets` | Create attendee and issue ticket if event capacity remains, otherwise join the waitlist. 409 if that attendee already holds a ticket for the event. |
| ET-01 | `GET /tickets/{id}` | Return ticket details for the ticket owner/demo flow. |
| ET-02, ET-03 | `POST /scans` | Validate a ticket QR or ticket ID at a gate. |
| ET-04 | `GET /gates/status` | Return live scan counts and last synced time by gate. |
| ET-07 | `GET /me/registrations` | Return the caller's own waitlist places, with live position and any ticket issued by a promotion, plus the tickets they currently hold. |
| User | `GET /me/ticket-stats` | Return the caller's ticket totals, tier split, and per-ticket check-in gate. |
| Scanner | `GET /me/scans` | Return the caller's scan totals, per-gate counts, and recent scan history. |
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

## Who owns a ticket

A ticket records the account that requested it in `tickets.user_id`, and the
person the seat is for in `tickets.attendee_id`. Those are deliberately separate:
one account can book several different attendees for the same event, which the
registration form has always allowed.

Two rules follow from that, both in `issue_ticket`:

- **One seat per person per event.** A second active ticket for the same
  `(user, attendee, event)` is a `409`. Without it a double click or a retry
  after a dropped connection hands out a second seat and silently shrinks the
  event. The check runs *before* the capacity test, so someone who already holds
  a seat is never pushed into the waitlist because the event filled up after they
  booked. Revoked tickets do not count, so a revoked person may register again.
- **Promotions carry the owner.** `promote_next_waitlisted` passes the queue
  entry's `user_id` onto the ticket it issues, so a ticket handed over by a
  revocation lands in that account's "my tickets" as well as being reachable
  through the waitlist entry.

`tickets.user_id` is nullable and is not backfilled by
`migrate_existing_database()`: a pre-existing ticket has no recorded owner, and
the attendee is not the account that booked it. Those tickets simply do not
appear in anyone's "my tickets" list.

## How event suggestions work

`GET /me/event-suggestions` powers the "Recommended for you" row. The split
matters more than the wording:

- **`recommend.py` decides what appears.** Pure functions, no network, no model.
  Candidates are upcoming events the caller has not registered for. Each is
  scored on two independent groups of signal, either of which can be empty:
  **own history** (tier preference, venues used, topic overlap with their
  events) and **the crowd** (popularity, and co-occurrence with the events the
  caller has booked). Weights are in one dict, `SUGGESTION_WEIGHTS`.
- **`groq.py` only rewrites the copy.** It is given the shortlist and asked for
  one sentence per event. Every returned id is checked against that shortlist,
  so a hallucinated event costs a sentence rather than a row.

Two deliberate consequences:

- **A model outage changes the wording, never the selection.** No key, a
  timeout, a 404, or unparseable JSON all fall back to the rule-based reason
  that `recommend.py` already generated, and the endpoint answers `200` with
  `ai_enabled: false`. This is the opposite of the Google login integration,
  which returns 503 when unconfigured — a supplementary row should degrade
  rather than error.
- **Nothing waits on the model.** The ranking is recomputed on every request;
  only the copy comes from a cache. On a cold cache the rule-based reason is
  returned immediately and the Groq call is queued as a FastAPI background
  task, so no request pays its 1-3 seconds. The cache is keyed on a digest of
  the candidate set, their seat counts, and the caller's own registrations, so
  copy describing "3 seats left" cannot outlive the last seat being taken.

### The privacy boundary

The prompt carries **event metadata only** — title, venue, date, remaining
seats, and tier counts. Attendee names, contact emails, campus ids, usernames,
and account ids never leave the process. `PromptEvent` in `groq.py` is a plain
value type with no column that could hold such a field, and
`test_the_prompt_carries_no_attendee_personal_data` asserts against the real
payload so the property cannot be lost by a later edit.

### Model configuration

`GROQ_MODEL` is configuration rather than a constant, because providers retire
model ids on their own schedule and a retired one fails with `404`. On a 404 the
fallback model is tried once, then the feature quietly shows rule-based copy.
Default `openai/gpt-oss-120b`, fallback `openai/gpt-oss-20b`.

### Cold start

TicketX currently has very little registration history, so most attendees will
see crowd-based copy rather than personal copy, and the row says so in its
heading rather than pretending otherwise. Both the crowd index and the own-history
signals improve as real registrations accumulate; nothing here needs changing
for that to happen, only data.

The crowd index is keyed on `attendee_id` rather than `user_id` deliberately:
that is the identity that survives a missing `tickets.user_id`, so tickets issued
before ticket ownership existed still contribute.

### How the scoring avoids saying something untrue

The interesting failures in this feature were all sentences that were technically
true and practically misleading, so the rules that prevent them are worth knowing
before changing the weights:

- **The default tier is not a preference.** "General" is what almost everyone
  takes, so a tier signal is measured as *excess* preference against the crowd's
  own tier mix. Otherwise a general-only attendee would be told "You usually pick
  general seating" with full confidence, and that near-meaningless signal would
  outrank ones that actually distinguish them.
- **One observation is not a habit.** Every personal signal is damped by
  `n / (n + 1)`, so a single registration cannot produce a confident-sounding
  reason.
- **Co-occurrence is a conditional share, not a count.** "Of the people who came
  to an event with you, this share also booked that" does not decay as the crowd
  grows, which a raw count does. The caller is excluded from the peer pool, and
  the reason names an event those peers genuinely attended.
- **A sold-out event never outranks a bookable one.** They are ordered as separate
  groups. A score penalty was tried first and could not work: a full event attracts
  registrations, which feeds it more signals, so any penalty large enough to
  matter distorted the bookable group too.
- **The model is told not to imply popularity.** A live model described a
  zero-registration event as "popular among attendees" because the prompt invited
  it. Real registration counts now go in, with an instruction not to reach for
  popularity language the count does not support.

## Demo data

The suggestion feature is hard to see on a real database, because there is barely
any registration history to rank from. A demo dataset exists for that, and it is
**off unless you turn it on**.

```powershell
python backend/scripts/seed_demo.py            # add the demo data
python backend/scripts/seed_demo.py --reset    # wipe it, then rebuild
python backend/scripts/seed_demo.py --status   # report, change nothing
```

Sign in as **`demo` / `demo123`**. That account has its own registrations, so the
row shows personal copy. Sign in as the standard `attendee` / `attendee123`
instead and the same row appears with crowd-based copy, which is the cold-start
path. Comparing the two accounts is the quickest way to see what the feature
actually does.

The dataset is built so each scoring signal is visible: the demo account's
registrations give it a venue history and a shared event, two other attendees
bridge that shared event to an event it has not seen, one event is popular but
unrelated, one is sold out so the waitlist state shows, and one event is in the
past so the "upcoming only" rule is visible rather than assumed.

> **Preview and demo use only.** This creates an account with a fixed, publicly
> documented password. Do not enable it on a deployment that real people hold
> accounts on.

To make a deployment demo-ready instead of seeding by hand, set
`TICKETX_SEED_DEMO=1` in the environment and restart. It runs at startup instead
of by hand, and it is idempotent, so a restart with the flag on will not duplicate
rows. `seed_demo_data` and the script call the same function, so both routes
produce an identical database. Demo rows are marked with a `DEMO-` campus prefix
and a fixed set of event titles, which is what lets `--reset` remove them without
touching real events or real accounts.

Note that enabling this costs a few real `GROQ_API_KEY` calls: the first browse
per account generates copy for the recommended events. The result is cached, so
repeat visits are free.

### Waiting is notified in-app

The promotion notice is in-app polling: the user workspace asks
`GET /me/registrations` every 15 seconds and shows a live position, plus a
"a ticket is ready for you" panel once a promotion lands. There is no email or
push channel yet. `promote_next_waitlisted` is the single place a promotion is
created, so adding one later means adding a send there, and `promoted_ticket_id`
already carries the recipient's ticket.

The same poll also carries `tickets`, the list behind "my tickets", so a
promotion shows up in the queue notice and the ticket list on the same tick and
the two can never disagree.

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
