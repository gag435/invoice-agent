"""Everything that touches the SQLite database."""
import sqlite3
from datetime import datetime

# item, stock, usual unit price (None = we don't know it)
SEED_INVENTORY = [
    ("WidgetA", 15, 250.00),
    ("WidgetB", 10, 500.00),
    ("GadgetX", 5, 750.00),
    ("FakeItem", 0, None),
]


def connect(path: str = "inventory.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # lets us read rows like dicts: row["stock"]
    return conn


def init_db(conn: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        conn.executescript("DROP TABLE IF EXISTS inventory; DROP TABLE IF EXISTS payments; DROP TABLE IF EXISTS rejections;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER NOT NULL, unit_price REAL);
        CREATE TABLE IF NOT EXISTS payments (invoice_number TEXT, vendor TEXT, amount REAL, paid_at TEXT);
        CREATE TABLE IF NOT EXISTS rejections (invoice_number TEXT, vendor TEXT, amount REAL, reasons TEXT, rejected_at TEXT);
        """
    )
    conn.executemany("INSERT OR IGNORE INTO inventory VALUES (?, ?, ?)", SEED_INVENTORY)
    conn.commit()


def get_item(conn: sqlite3.Connection, item: str) -> dict | None:
    row = conn.execute("SELECT * FROM inventory WHERE item = ?", (item,)).fetchone()
    return dict(row) if row else None


def already_paid(conn: sqlite3.Connection, invoice_number: str | None) -> bool:
    if not invoice_number:
        return False
    row = conn.execute("SELECT 1 FROM payments WHERE invoice_number = ?", (invoice_number,)).fetchone()
    return row is not None


def vendor_history(conn: sqlite3.Connection, vendor: str) -> dict:
    paid = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM payments WHERE vendor = ?", (vendor,)).fetchone()
    rejected = conn.execute("SELECT COUNT(*) FROM rejections WHERE vendor = ?", (vendor,)).fetchone()
    return {"vendor": vendor, "invoices_paid": paid[0], "total_paid": paid[1], "invoices_rejected": rejected[0]}


def record_payment(conn, invoice_number, vendor, amount) -> None:
    conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?)", (invoice_number, vendor, amount, datetime.now().isoformat()))
    conn.commit()


def record_rejection(conn, invoice_number, vendor, amount, reasons: list[str]) -> None:
    conn.execute(
        "INSERT INTO rejections VALUES (?, ?, ?, ?, ?)",
        (invoice_number, vendor, amount, " | ".join(reasons), datetime.now().isoformat()),
    )
    conn.commit()
