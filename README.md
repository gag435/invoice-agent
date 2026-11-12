# Acme Corp Invoice Agents

A multi-agent prototype that reads supplier invoices (TXT, PDF, JSON, CSV, XML), checks them against inventory, decides approve/reject with an LLM VP + auditor, and pays or logs the rejection.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py --invoice_path=data/invoices --reset_db     # whole folder
python main.py --invoice_path=data/invoices/invoice_1001.txt
pytest -q
```

Runs with **no API key** (rule-based fallbacks). To switch on the LLM agents:

```bash
export XAI_API_KEY="xai-..."      # optional: LLM_MODEL, LLM_BASE_URL for other OpenAI-compatible models
```

## How it works

| Stage | What it does | LLM? |
|---|---|---|
| 1. Ingestion | JSON/CSV/XML parsed directly. TXT/PDF sent to the LLM, which must return JSON matching a Pydantic schema; if the JSON is invalid or line items don't add up, the errors are sent back and it retries (max 3). | TXT/PDF only |
| 2. Validation | Deterministic rules: unknown items, zero stock, quantity over stock (summed per item), negative quantities, totals that don't add up, duplicates already paid, overpricing, pressure language, foreign currency. | No |
| 3. Approval | Any error = reject (the LLM can't override). Otherwise a VP agent decides using tools (`lookup_inventory`, `vendor_history`), an auditor agent critiques, and the VP revises. No agreement after 2 rounds = human review. | Yes |
| 4. Payment | `mock_payment()` + ledger entry, or rejection saved with reasons. | No |

Every step is logged as JSON to `logs/run.jsonl`, with a run id per invoice.

## Results on the sample data

8 paid, 12 rejected, 0 crashes; matches every scenario in the brief. Beyond the brief it also:
- blocks **duplicate payments**: INV-1004 (revised), INV-1011 and INV-1012 each arrive twice, worth $18,915 that would otherwise be paid twice;
- sums quantities per item across lines (INV-1013 has WidgetA on 3 lines = 22 vs 15 in stock);
- cleans scan typos ("2O26", "Widget A") and flags a 20% rush-order markup (INV-1010).

## Design decisions (business impact)

- **Code checks facts, the LLM makes judgment calls.** Stock and math checks are 100% reliable and free; the LLM is used only for messy text and borderline approvals. This targets the 30% error rate without adding new AI errors.
- **Fail safe.** Disagreement or a crash means reject and human review, never "pay".
- **Runs without a key.** Reviewers and CI can run everything offline.

## Assumptions and limitations

- Inventory stock = what we have received, so billing more than stock is treated as a billing error.
- A revised invoice with an already-paid number is rejected for manual review rather than paying the difference.
- Currency is flagged but not converted (the summary total mixes EUR and USD).
- Next steps: vendor master table, partial payments, a small web UI for the AP team.
