"""The shapes of data that move through the pipeline."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    item: str
    quantity: int
    unit_price: float | None = None
    note: str | None = None


class Invoice(BaseModel):
    invoice_number: str | None = None
    vendor: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    due_date_raw: str | None = None  # exactly what the document said, e.g. "yesterday"
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: float | None = None
    total: float | None = None
    currency: str = "USD"
    notes: str | None = None
    source_file: str = ""


class Issue(BaseModel):
    code: str                                   # e.g. "UNKNOWN_ITEM"
    severity: Literal["error", "warning"]       # error = must reject, warning = look closer
    message: str


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reasons: list[str]
