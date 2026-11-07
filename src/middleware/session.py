"""Signed session cookie middleware."""

import uuid

from starlette.requests import Request

from src.auth.session import sign_sid, verify_sid
from src.config import SESSION_COOKIE, SESSION_MAX_AGE


async def session_cookie(request: Request, call_next):
    """Browser ko signed session cookie do/refresh karo; API clients (header ya bina cookie) ko nahi."""
    path = request.url.path
    sid = verify_sid(request.cookies.get(SESSION_COOKIE))
    is_browser = path == "/" or "sec-fetch-mode" in request.headers  # curl ye header nahi bhejta
    issue = not path.startswith("/static") and not request.headers.get("x-session-id") and (
        path == "/" or (sid is None and is_browser)
    )
    if issue and sid is None:
        sid = uuid.uuid4().hex
    request.state.sid = sid
    response = await call_next(request)
    if issue:
        response.set_cookie(
            SESSION_COOKIE,
            sign_sid(sid),
            max_age=SESSION_MAX_AGE,
            httponly=True,  # JS (XSS) cookie padh nahi sakti
            samesite="lax",
            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        )
    return response
