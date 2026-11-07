"""Ingestion registry: session::file -> sha256/chunks.

Lambda par S3 me (shared, per-instance /tmp ke bajaye), local dev me file me.
"""

import json

from src import config
from src.db.s3 import s3


def registry_load() -> dict:
    """Registry padho (session::file -> sha256/chunks), Lambda par S3 se, locally file se."""
    if config.USE_S3:
        try:
            body = s3().get_object(Bucket=config.S3_BUCKET, Key=config.REGISTRY_KEY)["Body"].read()
            return json.loads(body)
        except Exception:
            return {}
    if config.REGISTRY_PATH.exists():
        try:
            return json.loads(config.REGISTRY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def registry_save(registry: dict) -> None:
    """Registry persist karo - Lambda par S3, locally ingested.json."""
    data = json.dumps(registry, indent=2)
    if config.USE_S3:
        s3().put_object(Bucket=config.S3_BUCKET, Key=config.REGISTRY_KEY, Body=data.encode("utf-8"))
    else:
        config.REGISTRY_PATH.write_text(data, encoding="utf-8")
