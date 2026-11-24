"""Verified backups (SQLite online-backup API) and row-count reports.

    python tools/backup_databases.py                 # back up app.db and checkpoints.sqlite
    python tools/backup_databases.py --counts        # row counts of the live databases
    python tools/backup_databases.py --counts --db X # row counts of specific files
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["app.db", "checkpoints.sqlite"]


def counts_for(path):
    conn = sqlite3.connect(path)
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def print_counts(path):
    print(f"\n{path}")
    for table, count in counts_for(path).items():
        print(f"  {table:<28}{count}")


def backup_all():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = ROOT / "backups" / stamp
    dest_dir.mkdir(parents=True, exist_ok=False)
    manifest = {}

    for name in SOURCES:
        source = ROOT / name
        if not source.exists():
            print(f"SKIP (not found): {name}")
            continue

        dest = dest_dir / name
        src_conn = sqlite3.connect(source)
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        check = sqlite3.connect(dest)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok":
            print(f"FAILED integrity check for {name}: {integrity}")
            sys.exit(1)

        manifest[name] = {
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            "rows": counts_for(dest),
        }
        print(f"Backed up {name} -> {dest}  (integrity: ok)")

    (dest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nBackup folder: {dest_dir}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--db", action="append", default=[])
    args = parser.parse_args()

    if args.counts:
        for path in args.db or [ROOT / n for n in SOURCES]:
            if Path(path).exists():
                print_counts(path)
        return
    backup_all()


if __name__ == "__main__":
    main()