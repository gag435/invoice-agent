"""Command line entry point.

Examples:
  python main.py --invoice_path=data/invoices/invoice_1001.txt
  python main.py --invoice_path=data/invoices            # every file in the folder
  python main.py --invoice_path=data/invoices --reset_db --json
"""
import argparse
import json
from pathlib import Path

from invoice_agent import db
from invoice_agent.llm import LLM
from invoice_agent.pipeline import process_invoice

SUPPORTED = {".txt", ".pdf", ".json", ".csv", ".xml"}


def print_result(r: dict) -> None:
    icon = {"paid": "PAID    ", "rejected": "REJECTED", "error": "ERROR   "}[r["status"]]
    if r["status"] == "error":
        print(f"[{icon}] {r['file']}: {r.get('error', 'payment failed')}")
        return
    total = f"{r['total']:,.2f} {r['currency']}" if r["total"] is not None else "?"
    print(f"[{icon}] {r['file']:<26} {r['invoice_number'] or '?':<9} {(r['vendor'] or '?')[:28]:<28} {total:>15}")
    for reason in r["reasons"]:
        print(f"             - {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Acme Corp invoice processing agents")
    parser.add_argument("--invoice_path", required=True, help="An invoice file, or a folder of invoices")
    parser.add_argument("--db", default="inventory.db", help="SQLite database path")
    parser.add_argument("--reset_db", action="store_true", help="Start fresh: re-seed inventory, forget past payments")
    parser.add_argument("--json", action="store_true", help="Print results as JSON instead of a table")
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.init_db(conn, reset=args.reset_db)
    llm = LLM()
    if not args.json:
        print(f"LLM: {'ON (' + llm.model + ')' if llm.enabled else 'OFF - using rule-based fallback'}\n")

    path = Path(args.invoice_path)
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED) if path.is_dir() else [path]

    results = [process_invoice(f, conn, llm) for f in files]

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print_result(r)
        paid = [r for r in results if r["status"] == "paid"]
        print(f"\n{len(paid)} paid (${sum(r['total'] for r in paid):,.2f}), "
              f"{sum(r['status'] == 'rejected' for r in results)} rejected, "
              f"{sum(r['status'] == 'error' for r in results)} errors. Full log: logs/run.jsonl")


if __name__ == "__main__":
    main()
