-- 004_llm_calls: per-attempt LLM telemetry.
-- Additive only. Never store prompts, invoice text, or raw provider errors.

CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    step TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    outcome TEXT NOT NULL,
    error_type TEXT,
    error_category TEXT,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    thinking_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    est_cost_usd REAL,
    pricing_version TEXT,
    started_at TEXT NOT NULL,
    created_at TEXT NOT NULL,

    CHECK (attempt >= 1),
    CHECK (outcome IN ('success', 'retryable_error', 'error', 'invalid_response')),
    CHECK (prompt_tokens IS NULL OR prompt_tokens >= 0),
    CHECK (output_tokens IS NULL OR output_tokens >= 0),
    CHECK (thinking_tokens IS NULL OR thinking_tokens >= 0),
    CHECK (total_tokens IS NULL OR total_tokens >= 0),
    CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CHECK (est_cost_usd IS NULL OR est_cost_usd >= 0)
);

CREATE INDEX idx_llm_calls_thread_id
    ON llm_calls(thread_id);

CREATE INDEX idx_llm_calls_created_at
    ON llm_calls(created_at);
