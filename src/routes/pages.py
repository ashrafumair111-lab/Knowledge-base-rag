"""GET / - chat page."""

from fastapi import APIRouter
from starlette.requests import Request

from src.controllers import pages_controller

router = APIRouter()


@router.get("/")
def home(request: Request):
    return pages_controller.home(request)
