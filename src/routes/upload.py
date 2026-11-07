"""Upload endpoints: direct upload, presign aur post-S3 ingest."""

from fastapi import APIRouter, Depends, File, UploadFile

from src.auth.session import session_id
from src.controllers import upload_controller
from src.models.upload import PresignRequest, UploadS3Request

router = APIRouter()


@router.post("/upload")
def upload_endpoint(file: UploadFile = File(...), sid: str = Depends(session_id)) -> dict:
    return upload_controller.upload_endpoint(file, sid)


@router.post("/presign")
def presign_endpoint(body: PresignRequest) -> dict:
    return upload_controller.presign_endpoint(body)


@router.post("/upload-s3")
def upload_s3_endpoint(body: UploadS3Request, sid: str = Depends(session_id)) -> dict:
    return upload_controller.upload_s3_endpoint(body, sid)
