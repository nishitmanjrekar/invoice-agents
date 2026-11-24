"""Extractor interface and an offline fake.

An extractor is any callable taking invoice text and returning a validated
Invoice. The default extractor is src.extract.extract_invoice (Gemini). The
fake below never touches the network, so tests need no API key.
"""
from collections.abc import Callable

from src.schemas import Invoice

Extractor = Callable[[str], Invoice]


class FakeExtractor:
    """Deterministic stand-in for Gemini extraction."""

    def __init__(self, invoice=None, *, by_text=None, error=None):
        self._default = invoice
        self._by_text = dict(by_text or {})
        self._error = error
        self.calls = []

    def __call__(self, invoice_text):
        self.calls.append(invoice_text)

        if self._error is not None:
            raise self._error

        invoice = self._by_text.get(invoice_text, self._default)

        if invoice is None:
            raise ValueError("FakeExtractor has no invoice configured for this text.")

        return invoice.model_copy(deep=True)