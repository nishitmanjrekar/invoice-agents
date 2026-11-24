import socket

import pytest

from src import db, db_core, migrate
from src.schemas import Invoice


@pytest.fixture(autouse=True)
def _never_touch_real_database(tmp_path, monkeypatch):
    """Point every test at a throwaway path so app.db can never be touched."""
    monkeypatch.setattr(db_core, "DB_PATH", tmp_path / "isolated_default.db")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests must never call Gemini or any remote host. Loopback stays allowed."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(f"Network access blocked in tests: {address}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """An empty database built by the migration system."""
    path = tmp_path / "test_app.db"
    monkeypatch.setattr(db_core, "DB_PATH", path)
    migrate.upgrade(path, create=True)
    return path


@pytest.fixture
def seeded_db(tmp_db):
    """tmp_db plus one approved vendor (Acme Supplies) and PO-2001 for 3000."""
    with db.db_connection() as conn:
        conn.execute(
            "INSERT INTO vendors (vendor_name, is_approved, created_at) VALUES (?, 1, ?)",
            ("Acme Supplies", db.current_timestamp()),
        )
        vendor_id = conn.execute(
            "SELECT id FROM vendors WHERE vendor_name = ?", ("Acme Supplies",)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO purchase_orders (vendor_id, po_number, po_amount, currency, created_at) "
            "VALUES (?, ?, ?, 'INR', ?)",
            (vendor_id, "PO-2001", 3000.0, db.current_timestamp()),
        )
    return tmp_db


@pytest.fixture
def make_invoice():
    def _make(**overrides):
        data = {
            "vendor": "Acme Supplies",
            "invoice_number": "INV-T-001",
            "invoice_date": "2026-11-06",
            "due_date": "2026-12-06",
            "currency": "INR",
            "line_items": [{"description": "Office chairs", "quantity": 2, "unit_price": 1500}],
            "total": 3000,
            "po_number": "PO-2001",
        }
        data.update(overrides)
        return Invoice.model_validate(data)

    return _make