import logging
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import db_core, passwords

logger = logging.getLogger(__name__)

ROLES = ("admin", "finance_manager", "reviewer", "uploader")
ISSUER = "invoice-agents"
ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=8)
MIN_SECRET_LENGTH = 32
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


class AuthError(Exception):
    pass


class AuthConfigError(AuthError):
    """The server is not configured for authentication (no usable secret)."""


class AuthUnavailable(AuthError):
    """The users table does not exist yet (migration 005 not applied)."""


class InvalidCredentials(AuthError):
    pass


class InvalidToken(AuthError):
    pass


class UserError(ValueError):
    pass


# ---------------------------------------------------------------- configuration
def auth_mode():
    """'off' or 'enforce'. Unknown values fail closed to 'enforce'."""
    value = os.getenv("INVOICE_AUTH", "off").strip().lower()
    return value if value in ("off", "enforce") else "enforce"


def auth_enforced():
    return auth_mode() == "enforce"


def get_secret():
    secret = os.getenv("INVOICE_AUTH_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthConfigError(
            f"INVOICE_AUTH_SECRET must be set to at least {MIN_SECRET_LENGTH} characters."
        )
    return secret


# ---------------------------------------------------------------------- users
def _now(now=None):
    return now or datetime.now(UTC)


def _open(db_path=None):
    conn = db_core.connect(db_path)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if exists is None:
        conn.close()
        raise AuthUnavailable("Authentication is unavailable: migration 005 is not applied.")
    return conn

def _postgres():
    return db_core.get_database_backend() == "postgresql"


def _get_postgres_auth():
    from src import auth_postgres
    return auth_postgres

def _public(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
    }


def create_user(username, password, role, *, db_path=None):
    """Create an active user. Returns the public user dict (never the hash)."""

    if _postgres():
        return _get_postgres_auth().create_user(
            username, password, role, db_path=db_path
        )
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        raise UserError(
            "Username must be 3-64 characters: letters, digits, dot, dash or underscore."
        )
    if role not in ROLES:
        raise UserError(f"Role must be one of: {', '.join(ROLES)}.")

    password_hash = passwords.hash_password(password)  # raises PasswordPolicyError
    stamp = _now().isoformat()
    conn = _open(db_path)

    try:
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, active, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (username, password_hash, role, stamp, stamp),
            )
        except sqlite3.IntegrityError as error:
            raise UserError("That username already exists.") from error
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _public(row)
    finally:
        conn.close()


def get_user(user_id):
    """Public user dict, or None. Reads the database on every call."""

    if _postgres():
        return _get_postgres_auth().get_user(user_id)
    conn = _open()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _public(row) if row is not None else None
    finally:
        conn.close()


def _record_failure(conn, row, now):
    stamp = now.isoformat()
    conn.execute(
        "UPDATE users SET failed_attempts = failed_attempts + 1, updated_at = ? WHERE id = ?",
        (stamp, row["id"]),
    )
    attempts = conn.execute(
        "SELECT failed_attempts FROM users WHERE id = ?", (row["id"],)
    ).fetchone()[0]

    if attempts >= MAX_FAILED_ATTEMPTS:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = ?, updated_at = ? "
            "WHERE id = ?",
            ((now + LOCKOUT).isoformat(), stamp, row["id"]),
        )
        logger.warning("Account locked after repeated failures (user id %s)", row["id"])

    conn.commit()


def authenticate(username, password, *, now=None):
    """
    Check credentials. Returns the public user dict or raises InvalidCredentials.

    Unknown user, wrong password, inactive account and locked account are not
    distinguishable to the caller, and a password check always runs so timing
    does not reveal which case applied.
    """

    if _postgres():
        return _get_postgres_auth().authenticate(
            username, password, now=now
        )
    now = _now(now)
    conn = _open()

    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if row is None:
            passwords.dummy_verify(password)
            raise InvalidCredentials()

        password_ok = passwords.verify_password(password, row["password_hash"])
        locked = bool(row["locked_until"]) and datetime.fromisoformat(row["locked_until"]) > now

        if locked or not row["active"]:
            raise InvalidCredentials()

        if not password_ok:
            _record_failure(conn, row, now)
            raise InvalidCredentials()

        stamp = now.isoformat()
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
            "last_login_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, row["id"]),
        )
        conn.commit()
        return _public(row)
    finally:
        conn.close()


# --------------------------------------------------------------------- tokens
def issue_token(user_id, *, now=None):
    now = _now(now)
    payload = {"sub": str(user_id), "iat": now, "exp": now + TOKEN_TTL, "iss": ISSUER}
    return jwt.encode(payload, get_secret(), algorithm=ALGORITHM)


def decode_token(token):
    """Return the user id from a valid token, or raise InvalidToken."""
    secret = get_secret()  # AuthConfigError propagates
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
        return int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as error:
        raise InvalidToken() from error


# --------------------------------------------------------------- dependencies
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail="Authentication required."):
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def _resolve(credentials):
    if credentials is None:
        raise _unauthorized()

    try:
        user = get_user(decode_token(credentials.credentials))
    except (AuthConfigError, AuthUnavailable) as error:
        logger.warning("Authentication unavailable: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Authentication is not available.") from error
    except InvalidToken as error:
        raise _unauthorized("Invalid or expired token.") from error

    if user is None or not user["active"]:
        raise _unauthorized("Invalid or expired token.")

    return user


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """Always requires a valid token. Returns the public user dict."""
    return _resolve(credentials)


def optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """None without a token; 401 for an invalid one."""
    return None if credentials is None else _resolve(credentials)


def require_roles(*roles):
    """Dependency factory: a valid token whose current role is one of `roles`."""
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"Unknown roles: {sorted(unknown)}")

    def dependency(user=Depends(current_user)):
        if roles and user["role"] not in roles:
            raise HTTPException(
                status_code=403, detail="Your role is not permitted to perform this action."
            )
        return user

    return dependency


def require_access(*roles):
    """
    Mode-aware dependency for Phase 2B.

    INVOICE_AUTH=off: never blocks; returns the user if a valid token was sent, else None.
    INVOICE_AUTH=enforce: same as require_roles(*roles).
    """
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"Unknown roles: {sorted(unknown)}")

    def dependency(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
        if not auth_enforced():
            try:
                return optional_user(credentials)
            except HTTPException:
                return None

        user = _resolve(credentials)
        if roles and user["role"] not in roles:
            raise HTTPException(
                status_code=403, detail="Your role is not permitted to perform this action."
            )
        return user

    return dependency