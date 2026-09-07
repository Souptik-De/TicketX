import hmac
import os
import re
from hashlib import sha256


SECRET = os.getenv("TICKETX_SECRET", "ticketx-dev-secret-change-before-production")

_TX_PATTERN = re.compile(r"TX-(\d+)\.([0-9a-fA-F]{64})", re.IGNORECASE)


def sign_ticket(ticket_id: int) -> str:
    signature = hmac.new(SECRET.encode(), f"ticket:{ticket_id}".encode(), sha256).hexdigest()
    return f"TX-{ticket_id}.{signature}"


def ticket_id_from_signature(qr_signature: str) -> int | None:
    # Accept raw TX plus camera/paste variants: surrounding whitespace,
    # lowercase prefix, or TX embedded in a URL / JSON wrapper.
    # This keeps generation (sign_ticket) and scan UI on the same TX.
    if not isinstance(qr_signature, str):
        return None
    text = qr_signature.strip()
    match = _TX_PATTERN.search(text)
    if match is None:
        return None
    try:
        ticket_id = int(match.group(1))
    except ValueError:
        return None
    signature = match.group(2).lower()

    expected = sign_ticket(ticket_id).split(".", maxsplit=1)[1]
    if not hmac.compare_digest(signature, expected):
        return None
    return ticket_id
