
from io import BytesIO

import pytest

from src import object_storage


@pytest.fixture
def storage_env(monkeypatch):
    monkeypatch.setenv(
        "OBJECT_STORAGE_ENDPOINT_URL",
        "https://test-project.supabase.co/storage/v1/s3",
    )
    monkeypatch.setenv("OBJECT_STORAGE_REGION", "ap-southeast-1")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "test-bucket")


def test_missing_storage_configuration(monkeypatch):
    for name in (
        "OBJECT_STORAGE_ENDPOINT_URL",
        "OBJECT_STORAGE_REGION",
        "OBJECT_STORAGE_ACCESS_KEY_ID",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "OBJECT_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="configuration is incomplete"):
        object_storage._get_storage_config()


def test_storage_configuration(storage_env):
    config = object_storage._get_storage_config()

    assert config == {
        "endpoint_url": (
            "https://test-project.supabase.co/storage/v1/s3"
        ),
        "region": "ap-southeast-1",
        "aws_access_key_id": "test-access",
        "aws_secret_access_key": "test-secret",
        "bucket": "test-bucket",
    }


def test_storage_client_configuration(storage_env, monkeypatch):
    calls = []

    def fake_client(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(object_storage.boto3, "client", fake_client)

    object_storage._get_storage_client()

    args, kwargs = calls[0]

    assert args == ("s3",)
    assert kwargs["endpoint_url"] == (
        "https://test-project.supabase.co/storage/v1/s3"
    )
    assert kwargs["region_name"] == "ap-southeast-1"
    assert kwargs["aws_access_key_id"] == "test-access"
    assert kwargs["aws_secret_access_key"] == "test-secret"

def test_check_storage_ready(storage_env, monkeypatch):
    calls = []

    client = type(
        "FakeClient",
        (),
        {
            "head_bucket": lambda self, **kwargs: calls.append(kwargs),
        },
    )()

    monkeypatch.setattr(
        object_storage,
        "_get_storage_client",
        lambda: client,
    )

    assert object_storage.check_storage_ready() is True

    assert calls == [{"Bucket": "test-bucket"}]


def test_check_storage_ready_propagates_error(
    storage_env,
    monkeypatch,
):
    def fail_head_bucket(self, **kwargs):
        raise RuntimeError("Storage unavailable")

    client = type(
        "FakeClient",
        (),
        {"head_bucket": fail_head_bucket},
    )()

    monkeypatch.setattr(
        object_storage,
        "_get_storage_client",
        lambda: client,
    )

    with pytest.raises(RuntimeError, match="Storage unavailable"):
        object_storage.check_storage_ready()


def test_upload_invoice(storage_env, monkeypatch):
    calls = []
    client = type(
        "FakeClient",
        (),
        {"put_object": lambda self, **kwargs: calls.append(kwargs)},
    )()

    monkeypatch.setattr(
        object_storage.boto3,
        "client",
        lambda *args, **kwargs: client,
    )

    result = object_storage.upload_invoice(
        "invoices/test.txt",
        b"invoice content",
    )

    assert result == "invoices/test.txt"
    assert calls == [
        {
            "Bucket": "test-bucket",
            "Key": "invoices/test.txt",
            "Body": b"invoice content",
            "ContentType": "text/plain",
        }
    ]


def test_upload_rejects_empty_key(storage_env):
    with pytest.raises(ValueError, match="object key cannot be empty"):
        object_storage.upload_invoice("", b"invoice")


@pytest.mark.parametrize("content", [b"", None, "invoice"])
def test_upload_rejects_invalid_content(storage_env, content):
    with pytest.raises(ValueError, match="non-empty bytes"):
        object_storage.upload_invoice("invoices/test.txt", content)


def test_delete_invoice(storage_env, monkeypatch):
    calls = []
    client = type(
        "FakeClient",
        (),
        {"delete_object": lambda self, **kwargs: calls.append(kwargs)},
    )()

    monkeypatch.setattr(
        object_storage.boto3,
        "client",
        lambda *args, **kwargs: client,
    )

    object_storage.delete_invoice("invoices/test.txt")

    assert calls == [
        {
            "Bucket": "test-bucket",
            "Key": "invoices/test.txt",
        }
    ]


def test_download_invoice_and_cleanup(storage_env, monkeypatch):
    body = BytesIO(b"invoice content")
    client = type(
        "FakeClient",
        (),
        {
            "get_object": lambda self, **kwargs: {"Body": body},
        },
    )()

    monkeypatch.setattr(
        object_storage.boto3,
        "client",
        lambda *args, **kwargs: client,
    )

    temporary_path = None

    with object_storage.download_invoice("invoices/test.txt") as path:
        temporary_path = path

        assert path.exists()
        assert path.read_bytes() == b"invoice content"

    assert temporary_path is not None
    assert not temporary_path.exists()
    assert body.closed


def test_download_closes_body_when_file_write_fails(
    storage_env,
    monkeypatch,
):
    body = BytesIO(b"invoice content")
    client = type(
        "FakeClient",
        (),
        {
            "get_object": lambda self, **kwargs: {"Body": body},
        },
    )()

    monkeypatch.setattr(
        object_storage.boto3,
        "client",
        lambda *args, **kwargs: client,
    )

    monkeypatch.setattr(
        object_storage.tempfile,
        "NamedTemporaryFile",
        lambda **kwargs: (_ for _ in ()).throw(OSError("disk error")),
    )

    with pytest.raises(OSError, match="disk error"):
        with object_storage.download_invoice("invoices/test.txt"):
            pass

    assert body.closed


def test_delete_rejects_empty_key(storage_env):
    with pytest.raises(ValueError, match="object key cannot be empty"):
        object_storage.delete_invoice("")
