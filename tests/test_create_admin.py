import shutil

import pytest

from src import auth, db_core, migrate, passwords
from tools import create_admin

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def _fast_scrypt(monkeypatch):
    monkeypatch.setattr(passwords, "N", 2**10)


def run(db, username="alice", password=PW, answer="y", **kwargs):
    return create_admin.run(
        username,
        db_path=db,
        prompt=lambda label: password,
        confirm=lambda label: answer,
        **kwargs,
    )


def users(db):
    conn = db_core.connect(db)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]
    finally:
        conn.close()


def test_creates_an_active_admin_with_a_hashed_password(tmp_db, capsys):
    assert run(tmp_db) == 0
    (user,) = users(tmp_db)
    assert (user["username"], user["role"], user["active"]) == ("alice", "admin", 1)
    assert user["password_hash"].startswith("scrypt$") and PW not in user["password_hash"]
    output = capsys.readouterr().out
    assert "Admin created" in output
    assert PW not in output and user["password_hash"] not in output


def test_the_created_admin_can_log_in(tmp_db, monkeypatch):
    run(tmp_db)
    assert auth.authenticate("alice", PW)["role"] == "admin"


def test_refuses_a_second_admin_unless_allowed(tmp_db):
    assert run(tmp_db) == 0
    assert run(tmp_db, username="bobby") == 1
    assert len(users(tmp_db)) == 1
    assert run(tmp_db, username="bobby", allow_additional=True) == 0
    assert len(users(tmp_db)) == 2


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe"])
def test_declining_the_confirmation_writes_nothing(tmp_db, answer):
    assert run(tmp_db, answer=answer) == 1
    assert users(tmp_db) == []


def test_mismatched_passwords_write_nothing(tmp_db):
    answers = iter([PW, PW + "x"])
    code = create_admin.run(
        "alice", db_path=tmp_db, prompt=lambda label: next(answers), confirm=lambda label: "y"
    )
    assert code == 1 and users(tmp_db) == []


def test_weak_password_and_bad_username_write_nothing(tmp_db, capsys):
    assert run(tmp_db, password="short") == 1
    assert run(tmp_db, username="no good") == 1
    assert users(tmp_db) == []
    assert "short" not in capsys.readouterr().out.replace("shortcoming", "")


def test_missing_database_is_refused_and_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    assert run(missing) == 1
    assert not missing.exists()


def test_default_target_is_the_configured_database_and_must_exist(tmp_db):
    assert create_admin.run(
        "alice", prompt=lambda label: PW, confirm=lambda label: "y"
    ) == 0
    assert len(users(tmp_db)) == 1


def test_database_without_the_users_table_is_refused(tmp_path, capsys):
    folder = tmp_path / "upto4"
    folder.mkdir()
    for path in sorted(migrate.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name[:3]) <= 4:
            shutil.copyfile(path, folder / path.name)
    old = tmp_path / "old.db"
    migrate.upgrade(old, create=True, migrations_dir=folder)
    assert run(old) == 1
    assert "migration 005" in capsys.readouterr().out