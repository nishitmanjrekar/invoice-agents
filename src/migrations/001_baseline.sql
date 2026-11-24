-- 001_baseline: the schema src.db.initialize_db() created before migrations existed.
-- Idempotent on purpose: applying it to an existing database changes nothing.

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_name TEXT NOT NULL UNIQUE,
    is_approved INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    po_number TEXT NOT NULL,
    po_amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    created_at TEXT NOT NULL,

    FOREIGN KEY (vendor_id)
        REFERENCES vendors(id),

    UNIQUE (vendor_id, po_number)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_date TEXT NOT NULL,
    due_date TEXT,
    currency TEXT NOT NULL,
    total REAL NOT NULL,
    po_number TEXT,
    extracted_data TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    created_at TEXT NOT NULL,

    FOREIGN KEY (vendor_id)
        REFERENCES vendors(id),

    UNIQUE (vendor_id, invoice_number)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,

    FOREIGN KEY (invoice_id)
        REFERENCES invoices(id)
);

CREATE TABLE IF NOT EXISTS invoice_review_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    case_type TEXT NOT NULL,
    invoice_data TEXT NOT NULL,
    validation_report TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    existing_invoice_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    reviewer_reason TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,

    FOREIGN KEY (existing_invoice_id)
        REFERENCES invoices(id),

    CHECK (
        case_type IN (
            'unknown_vendor',
            'duplicate'
        )
    ),

    CHECK (
        status IN (
            'open',
            'rejected',
            'resolved'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_invoice_number
    ON invoices(invoice_number);

CREATE INDEX IF NOT EXISTS idx_invoice_status
    ON invoices(status);

CREATE INDEX IF NOT EXISTS idx_po_number
    ON purchase_orders(po_number);

CREATE INDEX IF NOT EXISTS idx_review_case_status
    ON invoice_review_cases(status);