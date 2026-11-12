import json
from pathlib import Path

import pytest

from invoice_agent import db
from invoice_agent.ingestion import llm_extract
from invoice_agent.models import Invoice, LineItem
from invoice_agent.approval import decide
from invoice_agent.pipeline import process_invoice
from invoice_agent.validation import validate

INVOICES = Path(__file__).parent.parent / "data" / "invoices"


class NoLLM:
    enabled = False


class FakeLLM:
    """Plays back scripted replies so we can test the agent loops without an API key."""
    enabled = True
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        return reply if isinstance(reply, dict) else {"role": "assistant", "content": reply}


def no_log(*args, **kwargs):
    pass


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_db(c)
    return c


# Each invoice on a fresh database -> expected outcome and the issue code we expect to see
EXPECTED = [
    ("invoice_1001.txt", "paid", None),
    ("invoice_1002.txt", "rejected", "EXCEEDS_STOCK"),
    ("invoice_1003.txt", "rejected", "OUT_OF_STOCK"),
    ("invoice_1004.json", "paid", None),
    ("invoice_1005.json", "rejected", "EXCEEDS_STOCK"),
    ("invoice_1006.csv", "paid", None),
    ("invoice_1007.csv", "rejected", "EXCEEDS_STOCK"),
    ("invoice_1008.txt", "rejected", "UNKNOWN_ITEM"),
    ("invoice_1009.json", "rejected", "INVALID_QUANTITY"),
    ("invoice_1010.txt", "paid", "PRICE_ABOVE_USUAL"),
    ("invoice_1011.pdf", "paid", None),
    ("invoice_1012.pdf", "paid", None),
    ("invoice_1013.pdf", "rejected", "EXCEEDS_STOCK"),
    ("invoice_1014.xml", "paid", "FOREIGN_CURRENCY"),
    ("invoice_1015.csv", "paid", None),
    ("invoice_1016.json", "rejected", "UNKNOWN_ITEM"),
]


@pytest.mark.parametrize("filename,status,issue_code", EXPECTED)
def test_each_invoice(conn, filename, status, issue_code):
    result = process_invoice(INVOICES / filename, conn, NoLLM())
    assert result["status"] == status
    if issue_code:
        assert issue_code in [i["code"] for i in result["issues"]]


def test_fraud_invoice_raises_every_red_flag(conn):
    result = process_invoice(INVOICES / "invoice_1003.txt", conn, NoLLM())
    codes = {i["code"] for i in result["issues"]}
    assert {"OUT_OF_STOCK", "BAD_DUE_DATE", "PRESSURE_LANGUAGE"} <= codes


def test_duplicate_invoice_is_not_paid_twice(conn):
    assert process_invoice(INVOICES / "invoice_1004.json", conn, NoLLM())["status"] == "paid"
    second = process_invoice(INVOICES / "invoice_1004_revised.json", conn, NoLLM())
    assert second["status"] == "rejected"
    assert "DUPLICATE_INVOICE" in [i["code"] for i in second["issues"]]


def test_ingestion_self_corrects(tmp_path):
    """First answer has the wrong quantity; the agent must notice and ask again."""
    wrong = {"invoice_number": "INV-1", "vendor": "V", "line_items": [{"item": "WidgetA", "quantity": 1, "unit_price": 250}], "total": 500}
    right = {**wrong, "line_items": [{"item": "WidgetA", "quantity": 2, "unit_price": 250}]}
    llm = FakeLLM([json.dumps(wrong), "```json\n" + json.dumps(right) + "\n```"])

    inv = llm_extract("WidgetA qty 2 $250 ... Total $500", "x.txt", llm, no_log)

    assert inv.line_items[0].quantity == 2
    assert len(llm.calls) == 2
    assert "add up to" in llm.calls[1][-1]["content"]  # the feedback we sent back


def test_hard_rules_beat_the_llm(conn):
    inv = Invoice(invoice_number="INV-9", vendor="V", total=750, line_items=[LineItem(item="WidgetC", quantity=1, unit_price=750)])
    llm = FakeLLM([])  # would crash if called
    decision = decide(inv, validate(inv, conn), conn, llm, no_log)
    assert decision.decision == "reject"
    assert llm.calls == []


def test_vp_uses_tool_then_critic_pushes_back(conn):
    inv = Invoice(invoice_number="INV-7", vendor="V", total=12000, notes="URGENT wire transfer",
                  line_items=[LineItem(item="WidgetA", quantity=2, unit_price=6000)])
    tool_call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "vendor_history", "arguments": '{"vendor": "V"}'}}]}
    llm = FakeLLM([
        tool_call,                                                        # VP looks up the vendor
        '{"decision": "approve", "reasons": ["Looks fine"]}',             # VP round 1
        '{"agree": false, "feedback": "You ignored the price and the urgency"}',  # critic
        '{"decision": "reject", "reasons": ["Price 24x usual and pressure language"]}',  # VP round 2
        '{"agree": true, "feedback": "ok"}',                              # critic
    ])
    decision = decide(inv, validate(inv, conn), conn, llm, no_log)
    assert decision.decision == "reject"
    tool_results = [m for m in llm.calls[1] if m.get("role") == "tool"]
    assert json.loads(tool_results[0]["content"])["invoices_paid"] == 0
