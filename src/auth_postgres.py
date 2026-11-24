
"""PostgreSQL implementation of invoice-agent authentication."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src import passwords
from src.auth import AuthUnavailable, InvalidCredentials
from src.auth import UserError as UserError
from src.db_core import get_postgres_engine

logger = logging.getLogger(__name__)

ROLES = ("admin", "finance_manager", "reviewer", "uploader")
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)


def _now(now=None):
    return now or datetime.now(UTC)


def _public(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
    }


def _open():
    engine = get_postgres_engine()

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM users LIMIT 0"))
    except SQLAlchemyError as error:
        if getattr(error, "sqlstate", None) == "42P01":
            raise AuthUnavailable(
                "Authentication is unavailable: users table does not exist."
            ) from error
        raise

    return engine


def create_user(username, password, role, *, db_path=None):
    """Create an active user. Returns the public user dict."""
    from src.auth import USERNAME_PATTERN
    from src.auth import UserError as SharedUserError

    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        raise SharedUserError(
            "Username must be 3-64 characters: letters, digits, dot, dash or underscore."
        )

    if role not in ROLES:
        raise SharedUserError(f"Role must be one of: {', '.join(ROLES)}.")

    password_hash = passwords.hash_password(password)
    stamp = _now().isoformat()
    engine = _open()

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    INSERT INTO users (
                        username, password_hash, role, active,
                        created_at, updated_at
                    )
                    VALUES (
                        :username, :password_hash, :role, TRUE,
                        :created_at, :updated_at
                    )
                    RETURNING id, username, role, active
                """),
                {
                    "username": username,
                    "password_hash": password_hash,
                    "role": role,
                    "created_at": stamp,
                    "updated_at": stamp,
                },
            )
            row = result.mappings().one()

        return _public(row)

    except IntegrityError as error:
        raise SharedUserError("That username already exists.") from error


def get_user(user_id):
    """Read the current public user record."""
    engine = _open()

    with engine.connect() as connection:
        row = connection.execute(
            text("""
                SELECT id, username, role, active
                FROM users
                WHERE id = :user_id
            """),
            {"user_id": user_id},
        ).mappings().first()

    return _public(row) if row is not None else None



def authenticate(username, password, *, now=None):
    """
    Check credentials using a locked PostgreSQL row.

    Failed-login counters and lockouts are committed before
    InvalidCredentials is raised.
    """
    now = _now(now)
    engine = _open()
    authenticated_user = None
    invalid_credentials = False

    with engine.begin() as connection:
        row = connection.execute(
            text("""
                SELECT *
                FROM users
                WHERE LOWER(username) = LOWER(:username)
                FOR UPDATE
            """),
            {"username": username},
        ).mappings().first()

        if row is None:
            passwords.dummy_verify(password)
            invalid_credentials = True

        else:
            password_ok = passwords.verify_password(
                password, row["password_hash"]
            )

            locked = (
                bool(row["locked_until"])
                and datetime.fromisoformat(row["locked_until"]) > now
            )

            if locked or not row["active"]:
                invalid_credentials = True

            elif not password_ok:
                stamp = now.isoformat()

                connection.execute(
                    text("""
                        UPDATE users
                        SET failed_attempts = failed_attempts + 1,
                            updated_at = :updated_at
                        WHERE id = :user_id
                    """),
                    {"updated_at": stamp, "user_id": row["id"]},
                )

                attempts = connection.execute(
                    text("""
                        SELECT failed_attempts
                        FROM users
                        WHERE id = :user_id
                    """),
                    {"user_id": row["id"]},
                ).scalar_one()

                if attempts >= MAX_FAILED_ATTEMPTS:
                    connection.execute(
                        text("""
                            UPDATE users
                            SET failed_attempts = 0,
                                locked_until = :locked_until,
                                updated_at = :updated_at
                            WHERE id = :user_id
                        """),
                        {
                            "locked_until": (now + LOCKOUT).isoformat(),
                            "updated_at": stamp,
                            "user_id": row["id"],
                        },
                    )

                    logger.warning(
                        "Account locked after repeated failures (user id %s)",
                        row["id"],
                    )

                invalid_credentials = True

            else:
                stamp = now.isoformat()

                connection.execute(
                    text("""
                        UPDATE users
                        SET failed_attempts = 0,
                            locked_until = NULL,
                            last_login_at = :last_login_at,
                            updated_at = :updated_at
                        WHERE id = :user_id
                    """),
                    {
                        "last_login_at": stamp,
                        "updated_at": stamp,
                        "user_id": row["id"],
                    },
                )

                authenticated_user = _public(row)

    # The transaction has committed before credentials are rejected.
    if invalid_credentials:
        raise InvalidCredentials()

    return authenticated_user
