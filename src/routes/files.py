"""Session files + passages endpoints."""

from fastapi import APIRouter, Depends

from src.auth.session import session_id
from src.controllers import files_controller

router = APIRouter()


@router.get("/session/files")
def session_files(sid: str = Depends(session_id)) -> dict:
    return files_controller.session_files(sid)


@router.get("/session/passages")
def session_passages(source: str, sid: str = Depends(session_id)) -> dict:
    return files_controller.session_passages(source, sid)
