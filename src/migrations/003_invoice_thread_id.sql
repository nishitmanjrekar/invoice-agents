ALTER TABLE invoices ADD COLUMN thread_id TEXT;

CREATE UNIQUE INDEX idx_invoices_thread_id
    ON invoices(thread_id)
    WHERE thread_id IS NOT NULL;