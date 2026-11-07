"""S3 access - sirf deployed app par available (local dev par 400)."""

from fastapi import HTTPException

from src.config import USE_S3


def s3():
    """S3 client - Lambda runtime me boto3 pre-installed hota hai."""
    import boto3

    return boto3.client("s3")


def require_s3() -> None:
    """S3 sirf deployed app par hai - local dev par 400 do."""
    if not USE_S3:
        raise HTTPException(
            status_code=400, detail="S3 upload is only available on the deployed app"
        )
