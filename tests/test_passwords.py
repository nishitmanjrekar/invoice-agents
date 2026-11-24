import pytest

from src import passwords

PW = "correct horse battery"


def test_hash_format_and_parameters():
    hashed = passwords.hash_password(PW)
    assert hashed.startswith("scrypt$16384$8$1$")
    assert len(hashed.split("$")) == 6


def test_plaintext_is_not_in_the_hash():
    assert PW not in passwords.hash_password(PW)


def test_same_password_gets_a_different_salt_each_time():
    assert passwords.hash_password(PW) != passwords.hash_password(PW)


def test_verify_accepts_the_right_password_only():
    hashed = passwords.hash_password(PW)
    assert passwords.verify_password(PW, hashed) is True
    assert passwords.verify_password(PW + "x", hashed) is False
    assert passwords.verify_password("", hashed) is False


def test_verify_uses_the_parameters_stored_in_the_hash(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(passwords, "N", 2**10)
        weak = passwords.hash_password(PW)
    assert weak.startswith("scrypt$1024$")
    assert passwords.verify_password(PW, weak) is True


@pytest.mark.parametrize("password", ["a" * 11, "", "   ", " " * 12, "a" * 129, None, 123456789012])
def test_policy_rejects_bad_passwords(password):
    with pytest.raises(passwords.PasswordPolicyError):
        passwords.hash_password(password)


@pytest.mark.parametrize("password", ["a" * 12, "a" * 128, "pässwörd with spaces"])
def test_policy_accepts_valid_passwords(password):
    passwords.validate_password(password)


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "plaintext",
        "md5$1$2$3$a$b",
        "scrypt$notanumber$8$1$AAAA$AAAA",
        "scrypt$16384$8$1$AAAA",
        "scrypt$16384$8$1$!!!$!!!",
        "scrypt$1073741824$8$1$AAAA$AAAA",
        "scrypt$16384$99$1$AAAA$AAAA",
        "scrypt$1000$8$1$AAAA$AAAA",
    ],
)
def test_malformed_hashes_never_verify_and_never_raise(stored):
    assert passwords.verify_password(PW, stored) is False


def test_oversized_or_non_text_input_is_refused():
    hashed = passwords.hash_password(PW)
    assert passwords.verify_password("a" * 5000, hashed) is False
    assert passwords.verify_password(None, hashed) is False


def test_dummy_verify_is_always_false():
    assert passwords.dummy_verify(PW) is False
    assert passwords.dummy_verify("dummy-password-for-timing") is False