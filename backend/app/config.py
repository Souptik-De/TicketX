"""Environment configuration for the backend.

Importing this module loads ``backend/.env`` into the process. ``load_dotenv()``
does not override variables that are already set, so anything the shell or the
host's settings provide wins -- which is what production depends on, since the
``.env`` file is gitignored and does not exist there.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - the dependency is declared
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


# backend/.env sits next to the app package, one level up.
BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")


def get_groq_api_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


# Model ids are configuration rather than constants. Groq retires ids on its own
# schedule and a retired one fails with 404, so swapping a model is a settings
# change rather than a redeploy.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-20b"


def get_groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL


def get_groq_fallback_model() -> str:
    return os.getenv("GROQ_FALLBACK_MODEL", "").strip() or DEFAULT_FALLBACK_MODEL


def get_groq_timeout() -> float:
    raw = os.getenv("GROQ_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 8.0
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    # A negative or absurd timeout would turn the cache refresh into a hang.
    return value if 0 < value <= 60 else 8.0


#: Values accepted as "on" for TICKETX_SEED_DEMO. Anything else, including unset,
#: leaves the demo seed off -- so a production deployment has to opt in.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def demo_seed_enabled() -> bool:
    """Whether to populate the demo dataset at startup.

    Read per call rather than at import, so a change in the host's settings takes
    effect on the next restart instead of needing a code change.

    Leaving this on in production creates a public account with a known password
    (see ``DEMO_CREDENTIALS`` in seed.py). It is meant for internal previews and
    demos, not for a real deployment.
    """
    return os.getenv("TICKETX_SEED_DEMO", "").strip().lower() in _TRUTHY
