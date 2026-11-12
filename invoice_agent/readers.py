"""Turn any invoice file into either an Invoice (structured formats) or raw text (TXT/PDF)."""
import csv
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import pdfplumber

from .models import Invoice, LineItem

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%b %d %Y", "%B %d, %Y", "%d-%b-%Y", "%B %d %Y"]


def parse_date(value) -> date | None:
    """Try several date formats. Returns None for things like 'yesterday' or ''."""
    if not value:
        return None
    # "2O26" (letter O next to a digit) is a scanning typo for 2026
    text = re.sub(r"(?<=\d)O|O(?=\d)", "0", str(value).strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_number(value) -> float | None:
    """'$1,500.O0' -> 1500.0  (fixes letter O typos, strips $ and commas)."""
    if value is None or value == "":
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    text = re.sub(r"(?<=\d)O|O(?=\d)|(?<=\.)O", "0", text)
    try:
        return float(text)
    except ValueError:
        return None


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def read_structured(path: Path) -> Invoice | None:
    """JSON, CSV and XML already have labelled fields, so no AI is needed to read them."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _from_json(json.loads(path.read_text()), path)
    if suffix == ".csv":
        return _from_csv(path)
    if suffix == ".xml":
        return _from_xml(path)
    return None  # TXT / PDF -> needs the ingestion agent


def _from_json(d: dict, path: Path) -> Invoice:
    vendor = d.get("vendor")
    if isinstance(vendor, dict):
        vendor = vendor.get("name")
    return Invoice(
        invoice_number=d.get("invoice_number"),
        vendor=vendor or None,
        invoice_date=parse_date(d.get("date")),
        due_date=parse_date(d.get("due_date")),
        due_date_raw=d.get("due_date"),
        line_items=[
            LineItem(item=li["item"], quantity=li["quantity"], unit_price=li.get("unit_price"), note=li.get("note"))
            for li in d.get("line_items", [])
        ],
        subtotal=d.get("subtotal"),
        total=d.get("total"),
        currency=d.get("currency") or "USD",
        notes=d.get("notes"),
        source_file=path.name,
    )


def _from_csv(path: Path) -> Invoice:
    rows = list(csv.reader(path.read_text().splitlines()))
    header = [h.strip().lower() for h in rows[0]]

    if header == ["field", "value"]:  # "key,value" layout (invoice_1006.csv)
        inv = {"line_items": []}
        for key, value in rows[1:]:
            if key == "item":
                inv["line_items"].append({"item": value})
            elif key in ("quantity", "unit_price"):
                inv["line_items"][-1][key] = to_number(value)
            else:
                inv[key] = value
        d = {**inv, "subtotal": to_number(inv.get("subtotal")), "total": to_number(inv.get("total"))}
        return _from_json(d, path)

    # one-row-per-line-item layout (invoice_1007.csv, invoice_1015.csv)
    items, first, totals = [], None, {}
    for row in rows[1:]:
        if row[0].strip():
            first = first or row
            items.append(LineItem(item=row[4], quantity=int(row[5]), unit_price=to_number(row[6])))
        elif len(row) >= 8:  # summary rows like ",,,,,,Total:,15525.00"
            totals[row[6].strip().lower()] = to_number(row[7])
    return Invoice(
        invoice_number=first[0],
        vendor=first[1],
        invoice_date=parse_date(first[2]),
        due_date=parse_date(first[3]),
        due_date_raw=first[3],
        line_items=items,
        subtotal=totals.get("subtotal:"),
        total=totals.get("total:"),
        source_file=path.name,
    )


def _from_xml(path: Path) -> Invoice:
    root = ET.parse(path).getroot()
    get = lambda p: (root.findtext(p) or "").strip() or None  # noqa: E731
    return Invoice(
        invoice_number=get("header/invoice_number"),
        vendor=get("header/vendor"),
        invoice_date=parse_date(get("header/date")),
        due_date=parse_date(get("header/due_date")),
        due_date_raw=get("header/due_date"),
        currency=get("header/currency") or "USD",
        line_items=[
            LineItem(item=i.findtext("name"), quantity=int(i.findtext("quantity")), unit_price=to_number(i.findtext("unit_price")))
            for i in root.findall("line_items/item")
        ],
        subtotal=to_number(get("totals/subtotal")),
        total=to_number(get("totals/total")),
        source_file=path.name,
    )
