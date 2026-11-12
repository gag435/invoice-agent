"""Stage 2 - Validation: plain Python rules, no AI. Facts should be checked by code, not guessed by a model."""
from collections import defaultdict

from . import db
from .models import Invoice, Issue

SUSPICIOUS_WORDS = ["urgent", "immediately", "wire transfer", "gift card", "bitcoin"]
PRICE_TOLERANCE = 0.10  # flag prices more than 10% above our usual price


def validate(inv: Invoice, conn) -> list[Issue]:
    issues: list[Issue] = []
    err = lambda code, msg: issues.append(Issue(code=code, severity="error", message=msg))  # noqa: E731
    warn = lambda code, msg: issues.append(Issue(code=code, severity="warning", message=msg))  # noqa: E731

    # --- Data integrity -----------------------------------------------------
    if not inv.vendor:
        err("MISSING_VENDOR", "Vendor name is missing.")
    if not inv.invoice_number:
        err("MISSING_INVOICE_NUMBER", "Invoice number is missing.")
    if not inv.line_items:
        err("NO_LINE_ITEMS", "No line items were found.")
    if inv.total is None:
        err("MISSING_TOTAL", "Total amount is missing.")
    elif inv.total <= 0:
        err("INVALID_TOTAL", f"Total amount is {inv.total:.2f}, which is not a valid amount to pay.")
    for li in inv.line_items:
        if li.quantity <= 0:
            err("INVALID_QUANTITY", f"{li.item} has quantity {li.quantity}; quantities must be positive.")

    expected = inv.subtotal if inv.subtotal is not None else inv.total
    lines_sum = sum(li.quantity * (li.unit_price or 0) for li in inv.line_items)
    if expected is not None and inv.line_items and abs(lines_sum - expected) > 0.01:
        err("TOTAL_MISMATCH", f"Line items add up to {lines_sum:,.2f} but the invoice says {expected:,.2f}.")

    # --- Dates -------------------------------------------------------------
    if inv.due_date is None:
        raw = f" (it says '{inv.due_date_raw}')" if inv.due_date_raw else ""
        warn("BAD_DUE_DATE", f"Due date is missing or unreadable{raw}.")
    elif inv.invoice_date and inv.due_date < inv.invoice_date:
        warn("DUE_BEFORE_ISSUE", "Due date is before the invoice date.")

    # --- Inventory (add up quantities per item first: WidgetA can appear on several lines) ---
    qty_by_item: dict[str, int] = defaultdict(int)
    for li in inv.line_items:
        qty_by_item[li.item] += li.quantity

    for item, qty in qty_by_item.items():
        record = db.get_item(conn, item)
        if record is None:
            err("UNKNOWN_ITEM", f"{item} is not in our inventory.")
        elif record["stock"] == 0:
            err("OUT_OF_STOCK", f"{item} has 0 in stock - we cannot have received {qty}. Possible fraud.")
        elif qty > record["stock"]:
            err("EXCEEDS_STOCK", f"{item}: invoice bills {qty} but only {record['stock']} in stock.")

    for li in inv.line_items:
        record = db.get_item(conn, li.item)
        if record and record["unit_price"] and li.unit_price and li.unit_price > record["unit_price"] * (1 + PRICE_TOLERANCE):
            warn("PRICE_ABOVE_USUAL", f"{li.item} billed at {li.unit_price:.2f}, usual price is {record['unit_price']:.2f}.")

    # --- Fraud signals -----------------------------------------------------
    if db.already_paid(conn, inv.invoice_number):
        err("DUPLICATE_INVOICE", f"{inv.invoice_number} has already been paid.")
    text = (inv.notes or "").lower()
    hits = [w for w in SUSPICIOUS_WORDS if w in text]
    if hits:
        warn("PRESSURE_LANGUAGE", f"Notes contain pressure/fraud phrases: {', '.join(hits)}.")
    if inv.currency.upper() != "USD":
        warn("FOREIGN_CURRENCY", f"Invoice is in {inv.currency}; amounts were not converted.")

    return issues
