"""Upload controllers: direct upload + presigned S3 upload."""

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from src import config
from src.db import s3 as s3_db
from src.models.upload import PresignRequest, UploadS3Request
from src.services import ingest_service
from src.utils.files import safe_filename


def upload_endpoint(file: UploadFile, sid: str) -> dict:
    """User ki file lekar Qdrant mein ingest karta hai (skip / ingest / overwrite)."""
    return ingest_service.ingest_bytes(safe_filename(file.filename), file.file.read(), sid)


def presign_endpoint(body: PresignRequest) -> dict:
    """Browser ko temporary S3 PUT URL - badi files Lambda ke 6MB limit se bach jati hain."""
    s3_db.require_s3()
    filename = safe_filename(body.filename)
    if body.size and body.size > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 200 MB)")

    key = f"uploads/{uuid.uuid4().hex}-{filename}"
    url = s3_db.s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": config.S3_BUCKET, "Key": key},
        ExpiresIn=300,
    )
    return {"upload_url": url, "key": key}


def upload_s3_endpoint(body: UploadS3Request, sid: str) -> dict:
    """Browser S3 me file daal chuka hai - yahan S3 se padh kar ingest hota hai."""
    s3_db.require_s3()
    if not body.key.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Invalid upload key")

    data = s3_db.s3().get_object(Bucket=config.S3_BUCKET, Key=body.key)["Body"].read()
    # presign ne "<uuid>-<filename>" banaya tha - asli naam wapas nikaalo
    filename = safe_filename(Path(body.key).name.split("-", 1)[-1])
    return ingest_service.ingest_bytes(filename, data, sid)
