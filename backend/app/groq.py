"""Groq-backed copy for the attendee's event suggestions.

Scope is deliberately narrow. ``recommend.py`` has already decided which events
appear; this module only rewrites the one-line reason, and every returned value
is validated against that same shortlist. A model that invents an event, returns
an id we did not offer, or replies with something unusable costs a sentence, not
a row: the caller keeps the rule-based copy instead.

Two properties are load-bearing here:

* **Nothing blocks on the model.** Callers read the cache, return immediately
  with rule copy if the cache is cold, and schedule the refresh themselves.
* **No personal data leaves the process.** The prompt carries event titles,
  venues, dates, tiers, and counts only -- never attendee names, contact
  details, campus ids, usernames, or account ids. ``test_suggestions.py`` asserts
  this against the real payload, because it is the one property here that is
  invisible in review and easy to break by accident.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import re
import threading
import time

import httpx

from .config import (
    get_groq_api_key,
    get_groq_fallback_model,
    get_groq_model,
    get_groq_timeout,
)

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

#: How long a generated reason stays good for, and how many attendees to keep.
CACHE_TTL_SECONDS = 6 * 60 * 60
CACHE_MAX_ENTRIES = 500

#: A reason is a single line under an event card. Anything longer is a failure.
MAX_REASON_LENGTH = 140

_JSON_START = re.compile(r"\{")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class PromptEvent:
    """The plain event data the prompt is allowed to see.

    Deliberately a value type rather than the ``Event`` ORM row: the refresh runs
    after the response has been sent, by which point the request's session is
    closed and an ORM instance would be detached. Materialising the fields here
    also makes the no-personal-data boundary structural -- there is no column on
    this object that could carry an attendee's name or email.
    """

    id: int
    title: str
    venue: str
    date_time: datetime
    capacity: int
    seats_left: int
    tiers: str
    #: Registrations this event already has. Sent so the model can be told not to
    #: call a zero-registration event popular, which it otherwise will.
    registered: int


@dataclass(frozen=True)
class PromptSuggestion:
    event: PromptEvent
    signal: str


@dataclass
class CachedCopy:
    signature: str
    reasons: dict[int, str]
    generated_at: float = field(default_factory=time.time)

    def is_fresh(self, signature: str, now: float | None = None) -> bool:
        moment = now if now is not None else time.time()
        return self.signature == signature and (moment - self.generated_at) < CACHE_TTL_SECONDS


_cache: dict[int, CachedCopy] = {}
_cache_lock = threading.Lock()


def is_configured() -> bool:
    """Whether a key is present.

    Read through this rather than importing ``get_groq_api_key`` at the call
    site, so there is a single thing to substitute when a test needs the feature
    to look configured or unconfigured.
    """
    return bool(get_groq_api_key())


def clear_cache() -> None:
    """Drop every cached reason. Used by the test suite."""
    with _cache_lock:
        _cache.clear()


def read_cache(user_id: int, signature: str) -> dict[int, str] | None:
    with _cache_lock:
        entry = _cache.get(user_id)
    if entry is None or not entry.is_fresh(signature):
        return None
    return dict(entry.reasons)


def _write_cache(user_id: int, signature: str, reasons: dict[int, str]) -> None:
    with _cache_lock:
        # Insertion-ordered eviction: cheap, and with a cap of a few hundred small
        # dicts the cost of an LRU is not worth the extra moving parts.
        while len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)), None)
        _cache[user_id] = CachedCopy(signature=signature, reasons=reasons)


def _clean(text: object) -> str | None:
    """A reason is usable only if it is a non-empty, single line, short string."""
    if not isinstance(text, str):
        return None
    cleaned = _CONTROL_CHARS.sub(" ", text).replace("\r", " ").replace("\n", " ")
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH:
        return None
    return cleaned


def _parse_payload(raw: str) -> dict[int, str] | None:
    """Pull ``event_id``/``reason`` pairs out of a completion.

    Tolerant by design. Reasoning-capable models can precede their answer with
    prose, so the first ``{`` is treated as the start of the JSON, and anything
    unparseable returns ``None`` rather than raising into the request path.
    """
    match = _JSON_START.search(raw or "")
    if match is None:
        return None
    try:
        parsed = json.loads(raw[match.start():])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    items = parsed.get("reasons")
    if not isinstance(items, list):
        return None

    found: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        event_id = item.get("event_id")
        if isinstance(event_id, str) and event_id.strip().lstrip("-").isdigit():
            event_id = int(event_id.strip())
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            continue
        reason = _clean(item.get("reason"))
        if reason:
            found[event_id] = reason
    # None means "nothing usable here", so a reply of well-formed JSON that simply
    # contains no valid entry is treated the same as a reply that is not JSON.
    return found or None


def _build_prompt(suggestions, cold_start: bool) -> list[dict[str, str]]:
    """The request body.

    Only event metadata and the signal category are included. The signal is a
    label like ``own_venue``; the attendee's name, email, campus id, and account
    id are not available here and must not be added.
    """
    lines = []
    for suggestion in suggestions:
        event = suggestion.event
        lines.append(
            f"- event_id {event.id}: {event.title} | venue: {event.venue} | "
            f"date: {event.date_time:%d %b %Y} | registered: {event.registered} "
            f"| seats left: {event.seats_left} of {event.capacity} | tiers: {event.tiers}"
        )
    catalogue = "\n".join(lines)

    context = (
        "The attendee has no registration history yet, so none of these is a personal match. "
        "Point at what the event itself offers."
        if cold_start
        else "These were picked from what this attendee has registered for before."
    )

    system = (
        "You write one short, friendly sentence explaining why an event was recommended to an attendee. "
        "Write plain, concrete sentences of at most 20 words. Never invent facts, prices, dates, prizes, "
        "or popularity. Only mention that an event is popular, busy, or in demand if its registered count "
        "is above zero, and otherwise say nothing about how many people are going. Never use markdown, "
        "bullet points, or quotation marks. "
        "Reply with JSON only, in the form {\"reasons\": [{\"event_id\": <int>, \"reason\": \"<sentence>\"}]}. "
        "Include every event_id you were given, exactly once."
    )
    user = f"{context}\n\nEvents:\n{catalogue}\n\nReturn one reason per event."
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _request(model: str, messages: list[dict[str, str]], timeout: float) -> tuple[int, str] | None:
    """One chat completion. Returns ``(status_code, content)`` or ``None`` on a
    transport failure, so the caller can treat every failure the same way."""
    response = httpx.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {get_groq_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_completion_tokens": 700,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        return response.status_code, response.text[:200]
    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        return 200, ""
    return 200, choices[0].get("message", {}).get("content") or ""


def write_reasons(
    user_id: int,
    signature: str,
    suggestions,
    *,
    cold_start: bool,
) -> dict[int, str]:
    """Ask Groq for copy for events that ``recommend`` has already chosen.

    Returns a mapping of event id to reason, containing only ids that were
    offered. An unconfigured key, a transport error, a timeout, a 404 on a
    retired model, or an unusable reply all return an empty mapping, which the
    caller treats as "keep the rule-based copy".
    """
    if not suggestions:
        return {}

    api_key = get_groq_api_key()
    if not api_key:
        return {}

    messages = _build_prompt(suggestions, cold_start)
    timeout = get_groq_timeout()

    for model in (get_groq_model(), get_groq_fallback_model()):
        try:
            result = _request(model, messages, timeout)
        except httpx.HTTPError as error:
            # Timeouts and connection resets are expected here: this runs after
            # the response has already been sent to the attendee.
            logger.info("Groq copy unavailable for model %s: %s", model, error)
            return {}
        if result is None:
            return {}

        status, content = result
        if status == 404:
            # A retired model id. Try the fallback once, then give up quietly.
            logger.warning("Groq model %s returned 404; trying the fallback", model)
            continue

        if status != 200:
            logger.info("Groq copy request failed with status %s", status)
            return {}

        parsed = _parse_payload(content)
        if not parsed:
            logger.info("Groq reply for model %s was not usable JSON", model)
            return {}

        offered = {suggestion.event.id for suggestion in suggestions}
        kept = {event_id: reason for event_id, reason in parsed.items() if event_id in offered}
        if kept:
            _write_cache(user_id, signature, kept)
        return kept

    return {}
