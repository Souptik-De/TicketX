import os

from fastapi import HTTPException, status


def get_google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def verify_google_id_token(id_token_str: str) -> dict:
    client_id = get_google_client_id()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login is not configured",
        )

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google login is not available",
        )

    try:
        info = google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), client_id
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google login",
        )

    issuer = info.get("iss", "")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google login",
        )

    if info.get("email_verified") is not True:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google email is not verified",
        )

    sub = str(info.get("sub", "")).strip()
    email = str(info.get("email", "")).strip().lower()
    name = str(info.get("name", "")).strip()
    if not sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google login",
        )

    return {"sub": sub, "email": email, "name": name}
