"""Populate the demo dataset from the command line.

    python backend/scripts/seed_demo.py            # add the demo data
    python backend/scripts/seed_demo.py --reset    # wipe the demo data, then rebuild
    python backend/scripts/seed_demo.py --status   # report, change nothing

The same function backs the TICKETX_SEED_DEMO startup flag, so this script and a
flagged deployment produce an identical database. Demo rows are marked with a
DEMO- campus prefix and a fixed set of event titles, which is what lets --reset
remove them without touching real events or real accounts.

WARNING: the demo account has a fixed, publicly documented password
(demo / demo123). This is for local work, previews, and demos. Do not enable it
on a deployment that real people hold accounts on.
"""

import argparse
import sys
from pathlib import Path

# Run from anywhere: put the repository root on the path so `backend.app` imports.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.database import SessionLocal, create_database  # noqa: E402
from backend.app.seed import (  # noqa: E402
    DEMO_CREDENTIALS,
    DEMO_EVENTS,
    clear_demo_data,
    demo_data_exists,
    seed_demo_data,
)


def report(db, title: str) -> None:
    from backend.app.models import Attendee, Event, Ticket, User, WaitlistEntry

    print(f"\n{title}")
    print("-" * len(title))
    print(f"  events            {db.query(Event).count()}")
    print(f"  tickets           {db.query(Ticket).count()}")
    print(f"  waitlist entries  {db.query(WaitlistEntry).count()}")
    print(f"  attendees         {db.query(Attendee).count()}")
    print(f"  users             {db.query(User).count()}")
    demo = db.query(User).filter(User.username == "demo").one_or_none()
    if demo is not None:
        owned = (
            db.query(Ticket)
            .filter(Ticket.user_id == demo.id, Ticket.status != "revoked")
            .count()
        )
        print(f"  demo account      {DEMO_CREDENTIALS}  ({owned} tickets of its own)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove the existing demo rows before rebuilding them",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="report what is in the database without changing it",
    )
    args = parser.parse_args()

    create_database()

    db = SessionLocal()
    try:
        if args.status:
            present = demo_data_exists(db)
            report(db, f"Demo data present: {'yes' if present else 'no'}")
            return 0

        if demo_data_exists(db) and not args.reset:
            print("Demo data is already present. Nothing to do.")
            print("Use --reset to rebuild it from scratch.")
            report(db, "Current state")
            return 0

        if args.reset:
            print("Removing existing demo rows...")
            clear_demo_data(db)

        result = seed_demo_data(db)
        print(f"Seeded {result['events']} demo events.")

        report(db, "Demo data")
        print("\nSign in with:")
        print(f"    {DEMO_CREDENTIALS}")
        print("\nSign in as the standard 'attendee / attendee123' account as well to see")
        print("the cold-start path: the same row, with crowd-based reasons instead of")
        print("personal ones.")
        print("\nRecommended events show model-written wording from the second browse")
        print("visit onward. The first visit uses rule-based wording while the copy is")
        print("being generated in the background.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
