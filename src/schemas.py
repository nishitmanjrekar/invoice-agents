
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LineItem(BaseModel):
    """Represents one item on an invoice."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=1,
        description="Description of the purchased item"
    )

    quantity: float = Field(
        gt=0,
        description="Quantity purchased"
    )

    unit_price: float = Field(
        ge=0,
        description="Price per unit in the invoice currency"
    )


class Invoice(BaseModel):
    """Validated invoice information extracted by Gemini."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True
    )

    vendor: str = Field(
        min_length=1,
        description="Name of the invoice vendor"
    )

    invoice_number: str = Field(
        min_length=1,
        description="Unique invoice number"
    )

    invoice_date: date = Field(
        description="Date on which the invoice was issued"
    )

    due_date: date | None = Field(
        default=None,
        description="Payment due date, if available"
    )

    currency: str = Field(
        default="INR",
        min_length=3,
        max_length=3,
        description="Three-letter ISO currency code"
    )

    line_items: list[LineItem] = Field(
        min_length=1,
        description="List of purchased items"
    )

    total: float = Field(
        gt=0,
        description="Final invoice amount"
    )

    po_number: str | None = Field(
        default=None,
        description="Associated purchase order number"
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value):
        value = value.upper()

        if not value.isalpha():
            raise ValueError("Currency must contain only letters")

        return value

    @field_validator("vendor", "invoice_number")
    @classmethod
    def validate_required_text(cls, value):
        if not value.strip():
            raise ValueError("This field cannot be empty")

        return value


if __name__ == "__main__":

    sample_invoice = {
        "vendor": "Acme Supplies",
        "invoice_number": "INV-1001",
        "invoice_date": "2026-10-15",
        "due_date": "2026-11-14",
        "currency": "INR",
        "line_items": [
            {
                "description": "Office chairs",
                "quantity": 2,
                "unit_price": 1500
            }
        ],
        "total": 3000,
        "po_number": "PO-2001"
    }

    invoice = Invoice.model_validate(sample_invoice)

    print("\nINVOICE VALIDATION SUCCESSFUL")
    print("=" * 40)

    print("Vendor:", invoice.vendor)
    print("Invoice Number:", invoice.invoice_number)
    print("Invoice Date:", invoice.invoice_date)
    print("Currency:", invoice.currency)
    print("Total:", invoice.total)
    print("PO Number:", invoice.po_number)

    print("=" * 40)
    print("All fields passed validation.")
