"""Signed session cookie: browser apna session id khud chun nahi sakta."""

import hashlib
import hmac

from fastapi import Header
from starlette.requests import Request

from src.config import SESSION_SECRET

_SECRET = SESSION_SECRET.encode()


def sign_sid(sid: str) -> str:
    return f"{sid}.{hmac.new(_SECRET, sid.encode(), hashlib.sha256).hexdigest()[:32]}"


def verify_sid(value: str | None) -> str | None:
    """Cookie value se session id nikalo; signature galat ho to None (client id chun nahi sakta)."""
    sid, _, _ = (value or "").partition(".")
    if sid and hmac.compare_digest(sign_sid(sid), value):
        return sid
    return None


def session_id(
    request: Request,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> str:
    """Request ka session id: explicit X-Session-Id header (API clients), warna signed cookie.

    Browser cookie se chalta hai, isliye refresh par uploads wahin rehte hain aur har visitor ka
    knowledge base alag rehta hai. Dono na ho (curl) to purana shared "default" session.
    """
    sid = x_session_id or getattr(request.state, "sid", None)
    return (sid or "default").strip()[:64] or "default"
