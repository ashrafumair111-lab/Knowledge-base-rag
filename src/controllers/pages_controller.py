"""Chat page controller."""

from starlette.requests import Request

from src.templating import templates


def home(request: Request):
    """Simple chat UI (Jinja2 template)."""
    return templates.TemplateResponse(request, "index.html")
