"""A tiny client for a chat API (xAI Grok by default)."""
import json
import os
import re

import requests


class LLM:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("XAI_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.x.ai/v1")
        self.model = os.getenv("LLM_MODEL", "grok-4.7")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Send messages, get back the assistant's reply message (a dict)."""
        body = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            body["tools"] = tools
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]


def parse_json(text: str) -> dict:
    """Pull the first {...} block out of a model reply (models sometimes wrap JSON in ```)."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in the reply")
    return json.loads(match.group(0))


def run_with_tools(llm, messages: list[dict], tools: dict, max_steps: int = 5) -> str:
    """Let the model call our Python functions until it gives a final text answer.

    tools = {"name": (python_function, json_schema_for_the_model)}
    """
    schemas = [{"type": "function", "function": schema} for _, schema in tools.values()]
    for _ in range(max_steps):
        reply = llm.chat(messages, tools=schemas)
        messages.append(reply)
        calls = reply.get("tool_calls") or []
        if not calls:
            return reply.get("content") or ""
        for call in calls:
            name = call["function"]["name"]
            args = json.loads(call["function"].get("arguments") or "{}")
            func, _ = tools[name]
            result = func(**args)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    raise RuntimeError("Model kept calling tools without giving an answer")
