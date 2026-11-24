"""Password hashing with scrypt (standard library).

Stored format: scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>
Passwords are never logged, returned or stored in clear text.
"""
import base64
import hashlib
import hmac
import os

N = 2**14
R = 8
P = 1
DKLEN = 32
SALT_BYTES = 16
MAXMEM = 64 * 1024 * 1024

MIN_LENGTH = 12
MAX_LENGTH = 128
_MAX_VERIFY_INPUT = 1024

_dummy_hash = None


class PasswordPolicyError(ValueError):
    pass


def validate_password(password):
    if not isinstance(password, str):
        raise PasswordPolicyError("Password must be text.")
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_LENGTH} characters.")
    if not password.strip():
        raise PasswordPolicyError("Password cannot be only whitespace.")


def _derive(password, salt, n, r, p):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=MAXMEM
    )


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def hash_password(password):
    """Validate the password policy, then return a salted scrypt hash string."""
    validate_password(password)
    salt = os.urandom(SALT_BYTES)
    digest = _derive(password, salt, N, R, P)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    """True only for a matching password. Any malformed input returns False."""
    try:
        if not isinstance(password, str) or len(password) > _MAX_VERIFY_INPUT:
            return False

        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        n, r, p = int(n), int(r), int(p)

        if scheme != "scrypt":
            return False
        if n < 2**10 or n & (n - 1) or not 1 <= r <= 16 or not 1 <= p <= 4:
            return False
        if 128 * n * r > MAXMEM // 2:
            return False

        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        actual = _derive(password, salt, n, r, p)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def dummy_verify(password):
    """Spend the same time as a real check (used for unknown users). Always False."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-for-timing")
    verify_password(password, _dummy_hash)
    return False