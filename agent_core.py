"""
agent_core.py — Core agentic loop using DeepSeek AI (OpenAI-compatible API).
"""

import json
import os
import sys
import requests
from datetime import datetime, date
from typing import Callable, Any, Optional

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

MODEL      = "deepseek-chat"
MAX_TOKENS = 2048
MAX_TURNS  = 15

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SYSTEM_PROMPT = f"""You are an all-round personal assistant for Waseem Ali at Al Dhuhayan family office in Saudi Arabia.
Today is {date.today():%Y-%m-%d}.

WHO YOU SERVE:
- Waseem Ali — family office CFO/treasurer managing Al Dhuhayan Group, Target Real Estate, 3CV, Thiqa, Omar Law Firm, Atad SA, Burger Eight, Mansour Contracting, and more.

WHAT YOU CAN DO:
1. READ FILES — Excel, PDF, Word docs from Waseem's OneDrive "Waseem Data" folder (~1,500+ files)
2. SEND FILES — send any file as a Telegram attachment when asked
3. SEARCH — search across all files for any keyword, name, amount, or date
4. CALCULATE — VAT (15%), profit/loss, returns, loan payments, currency, ratios
5. ANSWER QUESTIONS — about any entity, balance, transaction, investment, or document

KEY FILES TO KNOW:
- Task/Petty Cash Sheet.xlsx — petty cash ledger
- 2025 time deposit & murabaha.xlsx — treasury deposits
- Cash Report/ — monthly bank balances for all entities
- co. work/ — 3CV, Al Dhuhayan, Atad, Omar Law Firm, Hilal Invest financials
- Umair & Waseem/ — investment portfolios, Saudi DPM, private equity funds
- Shahzad to waseem/ — family NAV, portfolio history, US real estate

KSA RULES:
- VAT is 15%, filed monthly or quarterly via ZATCA
- All advice must respect Shari'ah compliance

HOW YOU WORK:
- Use one tool at a time and wait for the result before continuing.
- NEVER guess numbers — always read from files.
- For any action that writes or sends something, get approval first.
- Reply in the same language the user writes in (Arabic or English).
- When done, give a short clear summary.
"""


# ----------------------------------------------------------------------------
# Tool registry
# ----------------------------------------------------------------------------

_TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, schema: dict, requires_approval: bool = False):
    def wrapper(fn: Callable[..., Any]):
        _TOOLS[name] = {
            "fn": fn,
            "requires_approval": requires_approval,
            "spec": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            },
        }
        return fn
    return wrapper


def tool_specs() -> list[dict]:
    return [t["spec"] for t in _TOOLS.values()]


def run_tool(name: str, args: dict) -> str:
    entry = _TOOLS.get(name)
    if not entry:
        return f"ERROR: unknown tool '{name}'"
    if entry["requires_approval"] and not _approve(name, args):
        return "DENIED: the human declined this action. Do not retry it."
    try:
        result = entry["fn"](**args)
        return result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as e:
        return f"ERROR running {name}: {e}"


def _approve(name: str, args: dict) -> bool:
    print(f"\n  APPROVAL NEEDED -> {name}({json.dumps(args, ensure_ascii=False)})")
    return input("  Approve? [y/N] ").strip().lower() == "y"


# ----------------------------------------------------------------------------
# Built-in utility tools
# ----------------------------------------------------------------------------

@tool(
    name="get_today",
    description="Return today's date and the current ISO week number.",
    schema={"type": "object", "properties": {}, "required": []},
)
def get_today():
    now = datetime.now()
    return {"date": now.strftime("%Y-%m-%d"), "week": now.isocalendar().week}


@tool(
    name="record_note",
    description="Save a note or proposed action to the agent's log. This writes data.",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The note or proposed action to record."},
        },
        "required": ["text"],
    },
    requires_approval=True,
)
def record_note(text: str):
    with open("agent_notes.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {text}\n")
    return f"Saved note: {text}"


# ----------------------------------------------------------------------------
# Core agent loop (DeepSeek / OpenAI-compatible)
# ----------------------------------------------------------------------------

def run_agent(task: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": task},
    ]

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type":  "application/json",
    }

    for turn in range(1, MAX_TURNS + 1):
        payload = {
            "model":      MODEL,
            "messages":   messages,
            "tools":      tool_specs(),
            "tool_choice": "auto",
            "max_tokens": MAX_TOKENS,
        }

        r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()

        choice  = data["choices"][0]
        message = choice["message"]
        messages.append(message)

        # Print reasoning text if any
        if message.get("content"):
            print(f"\n[agent] {message['content']}")

        # No tool call — model is done
        if choice["finish_reason"] != "tool_calls" or not message.get("tool_calls"):
            return (message.get("content") or "").strip()

        # Run each tool call and feed results back
        for tc in message["tool_calls"]:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"])
            print(f"[tool] -> {fn_name}({json.dumps(fn_args, ensure_ascii=False)})")
            output = run_tool(fn_name, fn_args)
            print(f"[tool] <- {output[:300]}")
            messages.append({
                "role":         "tool",
                "tool_call_id": tc["id"],
                "content":      output,
            })

    return "Stopped: reached the maximum number of turns without finishing."


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "What pending tasks do I have?"
    print(f"TASK: {task}")
    if not DEEPSEEK_API_KEY:
        sys.exit("Set DEEPSEEK_API_KEY first.")
    summary = run_agent(task)
    print(f"\n{'='*60}\nDONE: {summary}")
