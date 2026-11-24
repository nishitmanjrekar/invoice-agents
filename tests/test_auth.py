import shutil
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src import auth, db_core, migrate, passwords

SECRET = "unit-test-secret-" + "x" * 32
PW = "correct horse battery"
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("INVOICE_AUTH_SECRET", SECRET)
    monkeypatch.delenv("INVOICE_AUTH", raising=False)
    monkeypatch.setattr(passwords, "N", 2**10)


def make_user(username="alice", password=PW, role="reviewer"):
    return auth.create_user(username, password, role)


def row(username):
    conn = db_core.connect()
    try:
        found = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(found) if found else None
    finally:
        conn.close()


def sql(statement, *args):
    conn = db_core.connect()
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- configuration ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [(None, "off"), ("off", "off"), (" OFF ", "off"), ("enforce", "enforce"),
     ("ENFORCE", "enforce"), ("garbage", "enforce"), ("", "enforce")],
)
def test_auth_mode(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("INVOICE_AUTH", raising=False)
    else:
        monkeypatch.setenv("INVOICE_AUTH", value)
    assert auth.auth_mode() == expected
    assert auth.auth_enforced() is (expected == "enforce")


def test_missing_or_short_secret_is_a_config_error_that_never_echoes_the_value(monkeypatch):
    monkeypatch.delenv("INVOICE_AUTH_SECRET")
    with pytest.raises(auth.AuthConfigError):
        auth.get_secret()
    monkeypatch.setenv("INVOICE_AUTH_SECRET", "short-secret-value")
    with pytest.raises(auth.AuthConfigError) as caught:
        auth.get_secret()
    assert "short-secret-value" not in str(caught.value)


# ---- users ------------------------------------------------------------------------------
def test_create_user_stores_only_a_scrypt_hash(tmp_db):
    user = make_user()
    assert set(user) == {"id", "username", "role", "active"}
    stored = row("alice")
    assert stored["password_hash"].startswith("scrypt$")
    assert PW not in stored["password_hash"]


@pytest.mark.parametrize("username", ["ab", "has space", "x" * 65, "bad/char", "", None])
def test_bad_usernames_are_refused(tmp_db, username):
    with pytest.raises(auth.UserError):
        auth.create_user(username, PW, "reviewer")


def test_bad_role_weak_password_and_duplicates_are_refused(tmp_db):
    with pytest.raises(auth.UserError):
        auth.create_user("alice", PW, "superuser")
    with pytest.raises(passwords.PasswordPolicyError):
        auth.create_user("alice", "short", "reviewer")
    make_user("Alice")
    with pytest.raises(auth.UserError):
        make_user("alice")


def test_get_user_reads_the_database_each_time(tmp_db):
    user = make_user()
    assert auth.get_user(user["id"])["role"] == "reviewer"
    sql("UPDATE users SET role = 'admin' WHERE id = ?", user["id"])
    assert auth.get_user(user["id"])["role"] == "admin"
    assert auth.get_user(9999) is None


# ---- authenticate -----------------------------------------------------------------------
def test_successful_login_records_the_time_and_returns_a_public_user(tmp_db):
    make_user()
    user = auth.authenticate("alice", PW, now=T0)
    assert user["username"] == "alice" and "password_hash" not in user
    assert row("alice")["last_login_at"] == T0.isoformat()


def test_username_is_case_insensitive(tmp_db):
    make_user("Alice")
    assert auth.authenticate("ALICE", PW)["username"] == "Alice"


def test_wrong_password_is_counted(tmp_db):
    make_user()
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("alice", "wrong password!!")
    assert row("alice")["failed_attempts"] == 1


def test_unknown_user_still_spends_a_password_check(tmp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(passwords, "dummy_verify", lambda password: calls.append(1) or False)
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("nobody", PW)
    assert calls == [1]


def test_inactive_user_cannot_log_in_and_is_not_counted(tmp_db):
    make_user()
    sql("UPDATE users SET active = 0")
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate("alice", PW)
    assert row("alice")["failed_attempts"] == 0


def test_lockout_and_recovery(tmp_db):
    make_user()
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.InvalidCredentials):
            auth.authenticate("alice", "wrong password!!", now=T0)

    stored = row("alice")
    assert stored["failed_attempts"] == 0
    assert datetime.fromisoformat(stored["locked_until"]) == T0 + auth.LOCKOUT

    with pytest.raises(auth.InvalidCredentials):  # right password, still locked
        auth.authenticate("alice", PW, now=T0 + timedelta(minutes=5))
    assert row("alice")["failed_attempts"] == 0  # attempts while locked are not counted

    user = auth.authenticate("alice", PW, now=T0 + auth.LOCKOUT + timedelta(seconds=1))
    assert user["username"] == "alice"
    assert row("alice")["locked_until"] is None


def test_success_resets_the_failure_counter(tmp_db):
    make_user()
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(auth.InvalidCredentials):
            auth.authenticate("alice", "wrong password!!")
    auth.authenticate("alice", PW)
    assert row("alice")["failed_attempts"] == 0


def test_authenticate_reports_a_missing_users_table(tmp_path, monkeypatch):
    folder = tmp_path / "upto4"
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= 4:
            shutil.copyfile(path, folder / path.name)
    old = tmp_path / "old.db"
    migrate.upgrade(old, create=True, migrations_dir=folder)
    monkeypatch.setattr(db_core, "DB_PATH", old)
    with pytest.raises(auth.AuthUnavailable):
        auth.authenticate("alice", PW)


# ---- tokens -----------------------------------------------------------------------------
def test_token_round_trip_and_eight_hour_lifetime():
    token = auth.issue_token(42)
    assert auth.decode_token(token) == 42
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 8 * 3600
    assert claims["iss"] == auth.ISSUER and claims["sub"] == "42"


def test_expired_token_is_rejected():
    token = auth.issue_token(1, now=datetime.now(UTC) - timedelta(hours=9))
    with pytest.raises(auth.InvalidToken):
        auth.decode_token(token)


def test_tampered_token_is_rejected():
    token = auth.issue_token(1)
    tail = "AA" if token[-2:] != "AA" else "BB"
    with pytest.raises(auth.InvalidToken):
        auth.decode_token(token[:-2] + tail)


def test_token_signed_with_another_secret_is_rejected():
    now = datetime.now(UTC)
    claims = {"sub": "1", "iat": now, "exp": now + timedelta(hours=1), "iss": auth.ISSUER}
    with pytest.raises(auth.InvalidToken):
        auth.decode_token(jwt.encode(claims, "another-secret-" + "y" * 32, algorithm="HS256"))


def test_unsigned_token_is_rejected():
    now = datetime.now(UTC)
    claims = {"sub": "1", "iat": now, "exp": now + timedelta(hours=1), "iss": auth.ISSUER}
    with pytest.raises(auth.InvalidToken):
        auth.decode_token(jwt.encode(claims, None, algorithm="none"))


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "1", "iss": auth.ISSUER},                       # no exp
        {"sub": "1", "exp": 4102444800, "iss": "someone-else"},  # wrong issuer
        {"sub": "abc", "exp": 4102444800, "iss": auth.ISSUER},   # non-numeric subject
        {"exp": 4102444800, "iss": auth.ISSUER},                 # no subject
    ],
)
def test_malformed_claims_are_rejected(claims):
    claims = {**claims, "iat": 1700000000}
    with pytest.raises(auth.InvalidToken):
        auth.decode_token(jwt.encode(claims, SECRET, algorithm="HS256"))


def test_garbage_token_is_rejected():
    with pytest.raises(auth.InvalidToken):
        auth.decode_token("not-a-token")


# ---- dependencies -----------------------------------------------------------------------
def build_app():
    app = FastAPI()

    @app.get("/me")
    def me(user=Depends(auth.current_user)):
        return user

    @app.get("/admin")
    def admin(user=Depends(auth.require_roles("admin"))):
        return user

    @app.get("/access")
    def access(user=Depends(auth.require_access("admin"))):
        return {"user": user}

    return TestClient(app)


def login_token(username="alice", role="reviewer"):
    user = make_user(username, role=role)
    return user, auth.issue_token(user["id"])


def test_missing_or_wrong_scheme_is_401(tmp_db):
    client = build_app()
    response = client.get("/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_invalid_token_is_401(tmp_db):
    assert build_app().get("/me", headers=bearer("garbage")).status_code == 401


def test_valid_token_returns_the_current_user(tmp_db):
    user, token = login_token()
    body = build_app().get("/me", headers=bearer(token)).json()
    assert body == user


def test_role_is_checked_against_the_database_on_every_request(tmp_db):
    user, token = login_token(role="admin")
    client = build_app()
    assert client.get("/admin", headers=bearer(token)).status_code == 200
    sql("UPDATE users SET role = 'uploader' WHERE id = ?", user["id"])
    assert client.get("/admin", headers=bearer(token)).status_code == 403


def test_deactivated_user_loses_access_immediately(tmp_db):
    user, token = login_token()
    client = build_app()
    assert client.get("/me", headers=bearer(token)).status_code == 200
    sql("UPDATE users SET active = 0 WHERE id = ?", user["id"])
    assert client.get("/me", headers=bearer(token)).status_code == 401


def test_deleted_user_token_is_rejected(tmp_db):
    user, token = login_token()
    sql("DELETE FROM users WHERE id = ?", user["id"])
    assert build_app().get("/me", headers=bearer(token)).status_code == 401


def test_dependency_returns_503_when_auth_is_not_configured(tmp_db, monkeypatch):
    _, token = login_token()
    monkeypatch.delenv("INVOICE_AUTH_SECRET")
    response = build_app().get("/me", headers=bearer(token))
    assert response.status_code == 503
    assert SECRET not in response.text


def test_require_access_in_off_mode_never_blocks(tmp_db):
    client = build_app()
    assert client.get("/access").json() == {"user": None}
    assert client.get("/access", headers=bearer("garbage")).json() == {"user": None}
    _, token = login_token(role="uploader")  # wrong role, but not enforced
    assert client.get("/access", headers=bearer(token)).json()["user"]["role"] == "uploader"


def test_require_access_in_enforce_mode(tmp_db, monkeypatch):
    monkeypatch.setenv("INVOICE_AUTH", "enforce")
    client = build_app()
    _, uploader_token = login_token("bob", role="uploader")
    _, admin_token = login_token("root", role="admin")
    assert client.get("/access").status_code == 401
    assert client.get("/access", headers=bearer(uploader_token)).status_code == 403
    assert client.get("/access", headers=bearer(admin_token)).status_code == 200


def test_unknown_roles_are_refused_when_building_dependencies():
    with pytest.raises(ValueError):
        auth.require_roles("god")
    with pytest.raises(ValueError):
        auth.require_access("god")