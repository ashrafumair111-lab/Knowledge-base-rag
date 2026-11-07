"""Upload request schemas (direct + presigned S3)."""

from pydantic import BaseModel


class PresignRequest(BaseModel):
    filename: str
    size: int = 0


class UploadS3Request(BaseModel):
    key: str
