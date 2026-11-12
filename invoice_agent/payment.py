"""Stage 4 - Payment: pay approved invoices, record rejected ones."""
from . import db
from .models import Decision, Invoice


def mock_payment(vendor: str, amount: float) -> dict:
    print(f"Paid {amount} to {vendor}")
    return {"status": "success"}


def settle(inv: Invoice, decision: Decision, conn, log) -> dict:
    if decision.decision == "approve":
        result = mock_payment(inv.vendor, inv.total)
        if result["status"] == "success":
            db.record_payment(conn, inv.invoice_number, inv.vendor, inv.total)
        log("payment", status=result["status"], vendor=inv.vendor, amount=inv.total)
        return {"status": "paid" if result["status"] == "success" else "error"}

    db.record_rejection(conn, inv.invoice_number, inv.vendor, inv.total, decision.reasons)
    log("payment", status="rejected", reasons=decision.reasons)
    return {"status": "rejected"}
