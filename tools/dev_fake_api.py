"""Run the real API on throwaway databases with an offline fake extractor.

    python -m tools.dev_fake_api        # serves http://127.0.0.1:8010

Uses a temp folder for app.db, checkpoints and uploads. Never calls Gemini and
never touches the project's databases. A file whose text contains FAILONCE fails
its first extraction, to demonstrate failure recording and explicit resubmission.
"""
import json
import tempfile
from pathlib import Path

import uvicorn
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src import api, db, db_core, migrate
from src.agents.invoice_graph import build_invoice_graph
from src.evaluate import build_invoice
from src.extractors import FakeExtractor

ROOT = Path(__file__).resolve().parent.parent
PORT = 8010


def load_dataset():
    known = {}
    for json_file in sorted((ROOT / "data").glob("inv_*.json")):
        text_file = json_file.with_suffix(".txt")
        if not text_file.exists():
            continue
        data = json.loads(json_file.read_text(encoding="utf-8"))
        text = text_file.read_text(encoding="utf-8")
        try:
            known[text] = build_invoice(data, text)
        except Exception:
            continue
    return known


def main():
    tmp = Path(tempfile.mkdtemp(prefix="invoice_dev_api_"))
    db_core.DB_PATH = tmp / "app.db"
    migrate.upgrade(db_core.DB_PATH, create=True)
    db.seed_vendors()
    db.seed_purchase_orders()

    api.CHECKPOINT_DB = tmp / "checkpoints.sqlite"
    api.UPLOAD_DIR = tmp / "uploads"

    known = load_dataset()
    fallback = next(iter(known.values()))
    failed_once = set()

    def fake_extract(text):
        if "FAILONCE" in text and text not in failed_once:
            failed_once.add(text)
            raise RuntimeError("Simulated Gemini outage")
        return known.get(text, fallback).model_copy(deep=True)

    extractor = FakeExtractor(by_text={}, invoice=fallback)
    extractor.__class__ = type("DevExtractor", (FakeExtractor,), {
        "__call__": lambda self, text: fake_extract(text),
    })

    def fake_get_graph(saver):
        saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[("src.schemas", "Invoice")])
        return build_invoice_graph(
            checkpointer=saver, human_in_the_loop=True, use_mcp=False, extractor=extractor
        )

    api.get_graph = fake_get_graph

    print(f"\nDEV API (offline, throwaway data in {tmp})")
    print(f"Point the frontend at it:  $env:INVOICE_API_URL='http://127.0.0.1:{PORT}'\n")
    uvicorn.run(api.app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()