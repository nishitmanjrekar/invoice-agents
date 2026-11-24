"""Create the first admin account (interactive; the password is never echoed or printed).

    python -m tools.create_admin --username alice [--db PATH] [--allow-additional]

Refuses to run if an active admin already exists unless --allow-additional is given,
and asks for confirmation naming the target database before writing anything.
"""
import argparse
import getpass
import sys
from pathlib import Path

from src import auth, db_core, passwords


def run(
    username,
    *,
    db_path=None,
    allow_additional=False,
    prompt=getpass.getpass,
    confirm=input,
):
    path = Path(db_path) if db_path else Path(db_core.DB_PATH)

    if not path.exists():
        print(f"ERROR: database not found: {path}")
        return 1

    conn = db_core.connect(path)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if has_table is None:
            print("ERROR: the users table does not exist. Apply migration 005 first:")
            print(f"       python -m src.migrate upgrade --db {path}")
            return 1
        admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    if admins and not allow_additional:
        print("ERROR: an active admin already exists. Use --allow-additional to add another.")
        return 1

    answer = confirm(f"Create admin '{username}' in {path}? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Cancelled. Nothing was written.")
        return 1

    first = prompt("Password: ")
    second = prompt("Repeat password: ")
    if first != second:
        print("ERROR: the passwords do not match. Nothing was written.")
        return 1

    try:
        user = auth.create_user(username, first, "admin", db_path=path)
    except (auth.UserError, passwords.PasswordPolicyError) as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Admin created: id={user['id']} username={user['username']}")
    return 0


def main(argv=None, **kwargs):
    parser = argparse.ArgumentParser(prog="python -m tools.create_admin")
    parser.add_argument("--username", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--allow-additional", action="store_true")
    args = parser.parse_args(argv)
    return run(args.username, db_path=args.db, allow_additional=args.allow_additional, **kwargs)


if __name__ == "__main__":
    sys.exit(main())