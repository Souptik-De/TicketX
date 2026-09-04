import base64
import hmac
import json
import os
from hashlib import pbkdf2_hmac, sha256
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


AUTH_SECRET = os.getenv("TICKETX_SECRET", "ticketx-dev-secret-change-before-production")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.getenv("TICKETX_PASSWORD_SALT", "ticketx-demo-salt").encode()
    digest = pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)


def create_token(user: User) -> str:
    payload = {"id": user.id, "username": user.username, "role": user.role, "display_name": user.display_name}
    payload_text = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(AUTH_SECRET.encode(), payload_text.encode(), sha256).hexdigest()
    return f"{payload_text}.{signature}"


def read_token(token: str) -> dict | None:
    try:
        payload_text, signature = token.split(".", maxsplit=1)
    except ValueError:
        return None

    expected = hmac.new(AUTH_SECRET.encode(), payload_text.encode(), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        return json.loads(base64.urlsafe_b64decode(payload_text.encode()))
    except (ValueError, json.JSONDecodeError):
        return None


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")

    payload = read_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    user = db.get(User, payload.get("id"))
    if user is None or user.username != payload.get("username"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    return user


def require_roles(*roles: str):
    def dependency(current_user: Annotated[User, Depends(get_current_user)]) -> User:
        if current_user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied for this role")
        return current_user

    return dependency
