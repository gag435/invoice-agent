"""Stage 3 - Approval: hard rules first, then a VP agent + critic agent for the judgment calls."""
import json

from . import db
from .llm import parse_json, run_with_tools
from .models import Decision, Invoice, Issue

SCRUTINY_THRESHOLD = 10_000
MAX_ROUNDS = 2

VP_PROMPT = f"""You are the VP of Finance at Acme Corp reviewing a supplier invoice that passed automatic checks.
Policy:
- Invoices over ${SCRUTINY_THRESHOLD:,} need extra scrutiny: approve only if every warning has a reasonable explanation.
- Treat pressure language, unusual prices, foreign currency and unreadable dates as risks you must address.
- You may call tools to look up inventory or the vendor's payment history.
Reply with ONLY JSON: {{"decision": "approve" or "reject", "reasons": ["short reason", ...]}}"""

CRITIC_PROMPT = """You are an internal auditor double-checking the VP's decision on an invoice.
Check the decision against the policy, the invoice and every warning. Did the VP ignore a risk or reject without cause?
Reply with ONLY JSON: {"agree": true or false, "feedback": "what is wrong, or 'ok'"}"""


def decide(inv: Invoice, issues: list[Issue], conn, llm, log) -> Decision:
    errors = [i.message for i in issues if i.severity == "error"]
    warnings = [i.message for i in issues if i.severity == "warning"]

    # 1) Hard rules. No model can override these.
    if errors:
        return Decision(decision="reject", reasons=errors + [f"Also: {w}" for w in warnings])

    # 2) No API key: simple, predictable rules
    if not llm.enabled:
        if (inv.total or 0) > SCRUTINY_THRESHOLD and warnings:
            return Decision(decision="reject", reasons=[f"Over ${SCRUTINY_THRESHOLD:,} with open warnings"] + warnings)
        return Decision(decision="approve", reasons=["Passed all checks"] + [f"Noted: {w}" for w in warnings])

    # 3) VP agent proposes -> critic reviews -> VP revises (reflection loop)
    return reflect_and_decide(inv, warnings, conn, llm, log)


def reflect_and_decide(inv: Invoice, warnings: list[str], conn, llm, log) -> Decision:
    case = json.dumps({"invoice": inv.model_dump(mode="json"), "warnings": warnings}, indent=2)
    tools = {
        "lookup_inventory": (
            lambda item: db.get_item(conn, item) or {"error": f"{item} not found"},
            {"name": "lookup_inventory", "description": "Get stock and usual price for an item",
             "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}},
        ),
        "vendor_history": (
            lambda vendor: db.vendor_history(conn, vendor),
            {"name": "vendor_history", "description": "How many invoices we've paid or rejected for a vendor",
             "parameters": {"type": "object", "properties": {"vendor": {"type": "string"}}, "required": ["vendor"]}},
        ),
    }
    vp_messages = [{"role": "system", "content": VP_PROMPT}, {"role": "user", "content": case}]

    decision = None
    for round_no in range(1, MAX_ROUNDS + 1):
        decision = Decision(**parse_json(run_with_tools(llm, vp_messages, tools)))
        log("approval", round=round_no, agent="vp", decision=decision.decision, reasons=decision.reasons)

        critic_reply = llm.chat([
            {"role": "system", "content": CRITIC_PROMPT},
            {"role": "user", "content": f"{case}\n\nVP decision:\n{decision.model_dump_json()}"},
        ])["content"]
        critique = parse_json(critic_reply)
        log("approval", round=round_no, agent="critic", agree=critique.get("agree"), feedback=critique.get("feedback"))
        if critique.get("agree"):
            return decision
        vp_messages.append({"role": "user", "content": f"An auditor challenged your decision: {critique.get('feedback')}\nReconsider and reply with JSON again."})

    # Still disagreeing after MAX_ROUNDS: play safe, send to a human
    return Decision(decision="reject", reasons=["VP agent and auditor could not agree - needs human review"] + decision.reasons)
