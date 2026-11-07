"""Jinja2 template environment (views/ folder ke liye)."""

from fastapi.templating import Jinja2Templates

from src.config import VIEWS_DIR

templates = Jinja2Templates(directory=str(VIEWS_DIR))
