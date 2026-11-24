import logging

import pytest
from fastapi.testclient import TestClient

from src import api, auth, passwords

SECRET = "route-test-secret-" + "z" * 32
PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("INVOICE_AUTH_SECRET", SECRET)
    monkeypatch.delenv("INVOICE_AUTH", raising=False)
    monkeypatch.setattr(passwords, "N", 2**10)


@pytest.fixture
def client(tmp_db):
    auth.create_user("alice", PW, "reviewer")
    return TestClient(api.app)


def login(client, username="alice", password=PW):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_login_returns_a_token_and_a_public_user(client):
    response = login(client)
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 8 * 3600
    assert body["user"]["username"] == "alice" and body["user"]["role"] == "reviewer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "password" not in response.text.lower()


def test_the_token_opens_auth_me(client):
    token = login(client).json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": 1, "username": "alice", "role": "reviewer", "active": True},
        "auth_mode": "off",
    }


def test_all_failures_look_identical(client):
    wrong = login(client, password="wrong password!!")
    unknown = login(client, username="nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password."}


def test_account_locks_after_repeated_failures(client):
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        assert login(client, password="wrong password!!").status_code == 401
    assert login(client).status_code == 401  # correct password, but locked


def test_me_needs_a_valid_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_deactivated_user_token_stops_working(client):
    from src import db_core

    token = login(client).json()["access_token"]
    conn = db_core.connect()
    conn.execute("UPDATE users SET active = 0")
    conn.commit()
    conn.close()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_login_without_a_secret_is_503_and_leaks_nothing(client, monkeypatch):
    monkeypatch.delenv("INVOICE_AUTH_SECRET")
    response = login(client)
    assert response.status_code == 503
    assert SECRET not in response.text


def test_login_before_migration_005_is_503(tmp_path, monkeypatch):
    import shutil

    from src import db_core, migrate

    folder = tmp_path / "upto4"
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= 4:
            shutil.copyfile(path, folder / path.name)
    old = tmp_path / "old.db"
    migrate.upgrade(old, create=True, migrations_dir=folder)
    monkeypatch.setattr(db_core, "DB_PATH", old)
    assert login(TestClient(api.app)).status_code == 503


@pytest.mark.parametrize("payload", [{}, {"username": "alice"}, {"password": PW},
                                     {"username": "", "password": PW}])
def test_malformed_login_bodies_are_422(client, payload):
    assert client.post("/auth/login", json=payload).status_code == 422


def test_neither_the_password_nor_the_token_is_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    body = login(client).json()
    login(client, password="another wrong password")
    assert PW not in caplog.text
    assert "another wrong password" not in caplog.text
    assert body["access_token"] not in caplog.text
    assert SECRET not in caplog.text


def test_phase_2a_enforces_nothing_on_existing_routes(client, monkeypatch):
    monkeypatch.setenv("INVOICE_AUTH", "enforce")
    assert client.get("/health").status_code == 200
    assert client.get("/invoices").status_code == 200
    assert client.get("/review-cases").status_code == 200