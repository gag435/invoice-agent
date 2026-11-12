"""The orchestrator: runs one invoice through all four stages and logs every step."""
import json
import uuid
from datetime import datetime
from pathlib import Path

from .approval import decide
from .ingestion import ingest
from .payment import settle
from .validation import validate

LOG_FILE = Path("logs/run.jsonl")


def make_logger(run_id: str, file_name: str):
    LOG_FILE.parent.mkdir(exist_ok=True)

    def log(stage: str, **fields):
        event = {"time": datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "file": file_name, "stage": stage, **fields}
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    return log


def process_invoice(path: Path, conn, llm) -> dict:
    run_id = uuid.uuid4().hex[:8]
    log = make_logger(run_id, path.name)
    try:
        invoice = ingest(path, llm, log)
        log("ingestion", result=invoice.model_dump(mode="json"))

        issues = validate(invoice, conn)
        log("validation", issues=[i.model_dump() for i in issues])

        decision = decide(invoice, issues, conn, llm, log)
        log("approval", decision=decision.decision, reasons=decision.reasons)

        payment = settle(invoice, decision, conn, log)
        status = payment["status"]
    except Exception as exc:  # one bad file must never crash the whole batch
        log("error", error=repr(exc))
        return {"file": path.name, "status": "error", "error": str(exc), "run_id": run_id}

    return {
        "file": path.name,
        "run_id": run_id,
        "invoice_number": invoice.invoice_number,
        "vendor": invoice.vendor,
        "total": invoice.total,
        "currency": invoice.currency,
        "status": status,
        "issues": [i.model_dump() for i in issues],
        "reasons": decision.reasons,
    }
