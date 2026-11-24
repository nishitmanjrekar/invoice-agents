"""S3-compatible object storage for invoice documents.

Supports Supabase Storage through its S3-compatible API using boto3.
Credentials are read from environment variables and never hardcoded.
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import boto3
from botocore.config import Config


def _get_storage_config():
    """Read and validate S3-compatible object storage configuration."""

    endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT_URL")
    region = os.getenv("OBJECT_STORAGE_REGION")
    access_key = os.getenv("OBJECT_STORAGE_ACCESS_KEY_ID")
    secret_key = os.getenv("OBJECT_STORAGE_SECRET_ACCESS_KEY")
    bucket = os.getenv("OBJECT_STORAGE_BUCKET")

    missing = [
        name
        for name, value in (
            ("OBJECT_STORAGE_ENDPOINT_URL", endpoint),
            ("OBJECT_STORAGE_REGION", region),
            ("OBJECT_STORAGE_ACCESS_KEY_ID", access_key),
            ("OBJECT_STORAGE_SECRET_ACCESS_KEY", secret_key),
            ("OBJECT_STORAGE_BUCKET", bucket),
        )
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Object storage configuration is incomplete: "
            + ", ".join(missing)
        )

    return {
        "endpoint_url": endpoint.rstrip("/"),
        "region": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "bucket": bucket,
    }


def _get_storage_client():
    """Create an S3-compatible storage client."""

    config = _get_storage_config()

    return boto3.client(
        "s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        region_name=config["region"],
        config=Config(signature_version="s3v4"),
    )


def check_storage_ready():
    """Verify that the configured object storage bucket is accessible."""

    config = _get_storage_config()
    client = _get_storage_client()

    client.head_bucket(Bucket=config["bucket"])

    return True


def upload_invoice(object_key: str, content: bytes):
    """Upload invoice bytes and return the object key."""

    if not object_key or not object_key.strip():
        raise ValueError("Invoice object key cannot be empty.")

    if not isinstance(content, bytes) or not content:
        raise ValueError("Invoice content must be non-empty bytes.")

    config = _get_storage_config()
    client = _get_storage_client()

    client.put_object(
        Bucket=config["bucket"],
        Key=object_key,
        Body=content,
        ContentType="text/plain",
    )

    return object_key


def delete_invoice(object_key: str):
    """Delete an invoice object."""

    if not object_key or not object_key.strip():
        raise ValueError("Invoice object key cannot be empty.")

    config = _get_storage_config()
    client = _get_storage_client()

    client.delete_object(
        Bucket=config["bucket"],
        Key=object_key,
    )


@contextmanager
def download_invoice(object_key: str):
    """Download an invoice to a temporary file.

    The temporary file is automatically removed when the context exits.
    """

    if not object_key or not object_key.strip():
        raise ValueError("Invoice object key cannot be empty.")

    config = _get_storage_config()
    client = _get_storage_client()

    response = client.get_object(
        Bucket=config["bucket"],
        Key=object_key,
    )

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="invoice-",
            suffix=".txt",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(response["Body"].read())

        yield temporary_path

    finally:
        response["Body"].close()

        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)