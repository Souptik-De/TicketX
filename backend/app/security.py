import hmac
import os
from hashlib import sha256


SECRET = os.getenv("TICKETX_SECRET", "ticketx-dev-secret-change-before-production")


def sign_ticket(ticket_id: int) -> str:
    signature = hmac.new(SECRET.encode(), f"ticket:{ticket_id}".encode(), sha256).hexdigest()
    return f"TX-{ticket_id}.{signature}"


def ticket_id_from_signature(qr_signature: str) -> int | None:
    try:
        prefix, signature = qr_signature.split(".", maxsplit=1)
        ticket_id = int(prefix.removeprefix("TX-"))
    except (AttributeError, ValueError):
        return None

    expected = sign_ticket(ticket_id).split(".", maxsplit=1)[1]
    if not hmac.compare_digest(signature, expected):
        return None
    return ticket_id
