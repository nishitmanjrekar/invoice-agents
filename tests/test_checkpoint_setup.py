
from contextlib import contextmanager

import pytest

from src import api, checkpoint_setup


class FakeSaver:
    def __init__(self):
        self.setup_calls = 0

    def setup(self):
        self.setup_calls += 1


def test_explicit_setup_initializes_postgres_checkpoint_tables(monkeypatch):
    saver = FakeSaver()
    received_urls = []

    @contextmanager
    def fake_from_conn_string(url):
        received_urls.append(url)
        yield saver

    class FakePostgresSaver:
        from_conn_string = staticmethod(fake_from_conn_string)

    monkeypatch.setattr(
        checkpoint_setup,
        "PostgresSaver",
        FakePostgresSaver,
    )

    database_url = (
        "postgresql+psycopg://user:password@localhost/invoices"
    )

    checkpoint_setup.setup_postgres_checkpointer(database_url)

    assert received_urls == [
        "postgresql://user:password@localhost/invoices"
    ]
    assert saver.setup_calls == 1


def test_explicit_setup_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        checkpoint_setup.setup_postgres_checkpointer()


def test_explicit_setup_rejects_non_postgres_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        checkpoint_setup.setup_postgres_checkpointer(
            "sqlite:///app.db"
        )


def test_postgres_api_checkpointer_does_not_run_setup(
    monkeypatch,
):
    saver = FakeSaver()
    received_urls = []

    @contextmanager
    def fake_from_conn_string(url):
        received_urls.append(url)
        yield saver

    monkeypatch.setattr(
        api,
        "get_database_backend",
        lambda: "postgresql",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@localhost/invoices",
    )
    monkeypatch.setattr(
        api.PostgresSaver,
        "from_conn_string",
        staticmethod(fake_from_conn_string),
    )

    with api.open_checkpointer() as opened_saver:
        assert opened_saver is saver

    assert len(received_urls) == 1
    assert saver.setup_calls == 0


def test_sqlite_api_checkpointer_still_runs_setup(
    monkeypatch,
    tmp_path,
):
    saver = FakeSaver()
    received_paths = []

    @contextmanager
    def fake_from_conn_string(path):
        received_paths.append(path)
        yield saver

    monkeypatch.setattr(
        api,
        "get_database_backend",
        lambda: "sqlite",
    )
    monkeypatch.setattr(
        api.SqliteSaver,
        "from_conn_string",
        staticmethod(fake_from_conn_string),
    )
    monkeypatch.setattr(
        api,
        "CHECKPOINT_DB",
        tmp_path / "checkpoints.sqlite",
    )

    with api.open_checkpointer() as opened_saver:
        assert opened_saver is saver

    assert received_paths == [
        str(tmp_path / "checkpoints.sqlite")
    ]
    assert saver.setup_calls == 1
