"""Stage 1 - Ingestion agent: file -> Invoice."""
import re
from pathlib import Path

from pydantic import ValidationError

from .llm import parse_json
from .models import Invoice, LineItem
from .readers import parse_date, read_structured, read_text, to_number

EXTRACT_PROMPT = """You extract data from messy supplier invoices for Acme Corp.
Return ONLY a JSON object with these keys:
  invoice_number (string like "INV-1002"), vendor (string), invoice_date (YYYY-MM-DD or null),
  due_date (YYYY-MM-DD or null), due_date_raw (the due date text exactly as written),
  line_items (list of {item, quantity, unit_price, note}), subtotal (number or null),
  total (number or null), currency (3-letter code, default "USD"), notes (string or null).
Rules:
- Fix obvious typos: letter O used as zero ("2O26" -> 2026), "Widget A" -> "WidgetA", "Gadget X" -> "GadgetX".
- Put extra words after an item name into "note", e.g. "WidgetA (rush order)" -> item "WidgetA", note "rush order".
- Never invent values. If something is missing or unreadable (e.g. due date "yesterday"), use null.
- Copy any notes or payment instructions into "notes"."""

MAX_ATTEMPTS = 3


def ingest(path: Path, llm, log) -> Invoice:
    structured = read_structured(path)
    if structured is not None:
        log("ingestion", method="structured-parser", file=path.name)
        return structured

    text = read_text(path)
    if not llm.enabled:
        log("ingestion", method="regex-fallback", file=path.name)
        return regex_extract(text, path.name)
    return llm_extract(text, path.name, llm, log)


def llm_extract(text: str, filename: str, llm, log) -> Invoice:
    """Ask the LLM, check its answer, and send problems back until it gets it right."""
    messages = [
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": f"Invoice text:\n\n{text}"},
    ]
    problems = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = llm.chat(messages)["content"]
        try:
            invoice = Invoice(**parse_json(reply), source_file=filename)
            problems = sanity_check(invoice)
        except (ValueError, ValidationError) as err:
            invoice, problems = None, [f"Your JSON was invalid: {err}"]

        log("ingestion", method="llm", attempt=attempt, problems=problems)
        if not problems:
            return invoice

        # Self-correction: show the model its own answer and what's wrong with it
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": "Please fix these problems and return the full JSON again:\n- " + "\n- ".join(problems)})

    if invoice is None:
        raise RuntimeError(f"Could not extract {filename} after {MAX_ATTEMPTS} attempts")
    return invoice  # best effort; validation will flag what's still wrong


def sanity_check(inv: Invoice) -> list[str]:
    """Cheap checks that catch extraction mistakes (not business rules - that's validation)."""
    problems = []
    if not inv.line_items:
        problems.append("No line items found.")
    if inv.total is None:
        problems.append("Total amount is missing.")
    expected = inv.subtotal if inv.subtotal is not None else inv.total
    lines_sum = sum(li.quantity * (li.unit_price or 0) for li in inv.line_items)
    if expected is not None and inv.line_items and abs(lines_sum - expected) > 0.01:
        problems.append(f"Line items add up to {lines_sum:.2f} but the invoice says {expected:.2f}. Re-read quantities and prices.")
    return problems


# ---------------------------------------------------------------------------
# Regex fallback: lets the whole system run with no API key at all.
# ---------------------------------------------------------------------------
ITEM_LINE = re.compile(
    r"^\s*-?\s*(?P<name>[A-Za-z][A-Za-z ]*?[A-Za-z])\s*(?:\((?P<note>[^)]*)\))?"
    r"\s+(?:qty:?\s*|x)?(?P<qty>-?\d+)\s+(?:unit price:\s*|@\s*)?\$(?P<price>[\d,\.O]+)",
    re.IGNORECASE,
)


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None


def regex_extract(text: str, filename: str) -> Invoice:
    vendor = _find(r"^\s*(?:Vendor|Vndr)\s*:\s*(.+?)(?:\s+Due:|\s{2,}|$)", text) or _find(r"^\s*FROM\s*:\s*(.+?)(?:\s{2,}|$)", text)

    number = _find(r"(INV[-\s]?\d{3,})", text) or _find(r"Inv(?:oice)?\s*(?:#|No\.?|Number)\s*:?\s*(\d{3,})", text)
    if number:
        number = "INV-" + re.sub(r"\D", "", number)

    due_raw = _find(r"(?:Due Date|Due Dt|Due)\s*:\s*(.+?)(?:\s{2,}|$)", text)
    date_raw = _find(r"(?:^|\s)(?:Date|Dt)\s*:\s*(.+?)(?:\s{2,}|\s+\w+:|$)", text)

    totals = re.findall(r"\b(?:grand\s+)?(?:total(?:\s+amount)?|amt)\s*:\s*\$?\s*(-?[\d,\.O]+)", text, re.IGNORECASE)
    subtotal = _find(r"\bsubtotal\s*:\s*\$?\s*([\d,\.O]+)", text)

    items = []
    for line in text.splitlines():
        m = ITEM_LINE.match(line)
        if m:
            items.append(
                LineItem(
                    item=m["name"].replace(" ", ""),  # "Widget A" -> "WidgetA"
                    quantity=int(m["qty"]),
                    unit_price=to_number(m["price"]),
                    note=m["note"],
                )
            )

    notes = _find(r"^\s*Notes?\s*:\s*(.+)$", text)
    return Invoice(
        invoice_number=number,
        vendor=vendor,
        invoice_date=parse_date(date_raw),
        due_date=parse_date(due_raw),
        due_date_raw=due_raw,
        line_items=items,
        subtotal=to_number(subtotal),
        total=to_number(totals[-1]) if totals else None,
        notes=notes,
        source_file=filename,
    )
