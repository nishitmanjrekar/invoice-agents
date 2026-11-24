
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError

from src import auth_postgres as auth


def _result(row=None, scalar=None):
    result = Mock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.one.return_value = row
    result.scalar_one.return_value = scalar
    return result


def _engine(connection):
    @contextmanager
    def begin():
        yield connection

    @contextmanager
    def connect():
        yield connection

    return SimpleNamespace(begin=begin, connect=connect)


def test_create_user_returns_public_user(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(
        {
            "id": 1,
            "username": "alice",
            "role": "reviewer",
            "active": True,
        }
    )

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(auth.passwords, "hash_password", lambda _: "scrypt$hash")

    user = auth.create_user("alice", "correct horse battery", "reviewer")

    assert user == {
        "id": 1,
        "username": "alice",
        "role": "reviewer",
        "active": True,
    }
    assert connection.execute.call_count == 1


def test_duplicate_username_raises_user_error(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = IntegrityError(
        "INSERT", {}, Exception("duplicate")
    )

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(auth.passwords, "hash_password", lambda _: "scrypt$hash")

    with pytest.raises(auth.UserError, match="username already exists"):
        auth.create_user("alice", "correct horse battery", "reviewer")


def test_get_user_returns_public_user(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(
        {
            "id": 2,
            "username": "bob",
            "role": "admin",
            "active": True,
        }
    )

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))

    user = auth.get_user(2)

    assert user["username"] == "bob"
    assert user["role"] == "admin"
    assert "password_hash" not in user


def test_successful_login_updates_login_timestamp(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 3,
                "username": "Alice",
                "password_hash": "hash",
                "role": "reviewer",
                "active": True,
                "locked_until": None,
            }
        ),
        _result(),
    ]

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(auth.passwords, "verify_password", lambda *_: True)

    user = auth.authenticate("ALICE", "password")

    assert user["username"] == "Alice"
    assert connection.execute.call_count == 2

    update_params = connection.execute.call_args_list[1].args[1]
    assert update_params["last_login_at"]


def test_wrong_password_commits_failure_counter(monkeypatch):
    transaction = Mock()
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 4,
                "username": "alice",
                "password_hash": "hash",
                "role": "reviewer",
                "active": True,
                "locked_until": None,
            }
        ),
        _result(),
        _result(scalar=1),
    ]

    @contextmanager
    def begin():
        try:
            yield connection
        except Exception:
            transaction.rollback()
            raise
        else:
            transaction.commit()

    monkeypatch.setattr(
        auth,
        "_open",
        lambda: SimpleNamespace(begin=begin),
    )
    monkeypatch.setattr(auth.passwords, "verify_password", lambda *_: False)

    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("alice", "wrong password")

    transaction.commit.assert_called_once()
    transaction.rollback.assert_not_called()
    assert connection.execute.call_count == 3


def test_fifth_failure_sets_lockout(monkeypatch):
    connection = Mock()
    connection.execute.side_effect = [
        _result(
            {
                "id": 5,
                "username": "alice",
                "password_hash": "hash",
                "role": "reviewer",
                "active": True,
                "locked_until": None,
            }
        ),
        _result(),
        _result(scalar=auth.MAX_FAILED_ATTEMPTS),
        _result(),
    ]

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(auth.passwords, "verify_password", lambda *_: False)

    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("alice", "wrong password")

    assert connection.execute.call_count == 4

    lockout_params = connection.execute.call_args_list[3].args[1]
    assert lockout_params["locked_until"]


def test_unknown_user_runs_dummy_password_check(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(None)

    calls = []
    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(
        auth.passwords,
        "dummy_verify",
        lambda password: calls.append(password),
    )

    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("nobody", "password")

    assert calls == ["password"]


def test_inactive_user_is_rejected_without_failure_update(monkeypatch):
    connection = Mock()
    connection.execute.return_value = _result(
        {
            "id": 6,
            "username": "alice",
            "password_hash": "hash",
            "role": "reviewer",
            "active": False,
            "locked_until": None,
        }
    )

    monkeypatch.setattr(auth, "_open", lambda: _engine(connection))
    monkeypatch.setattr(auth.passwords, "verify_password", lambda *_: True)

    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("alice", "password")

    assert connection.execute.call_count == 1
