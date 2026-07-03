"""
agent_core.py — Core agentic loop for an accounting assistant.

This is the "brain": a tool-calling loop built on the Anthropic Messages API.
It reads a task, lets Claude pick a tool, runs it, feeds the result back, and
repeats until the task is done. Tools are pluggable via the @tool decorator.

Run:
    pip install anthropic
    export ANTHROPIC_API_KEY="sk-ant-..."
    python agent_core.py "List my pending VAT tasks for this month"

Two demo tools are registered so the loop runs out of the box. Replace/extend
them with your Zoho Books, Excel, and email tools using the same pattern.
"""

import json
import os
import sys
import inspect
from datetime import datetime, date
from typing import Callable, Any, Optional

import anthropic

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"      # fast + capable for an agent loop; swap to claude-opus-4-8 for hard reasoning
MAX_TOKENS = 2048
MAX_TURNS = 15                   # safety cap so the loop can't run forever

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

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")


# ----------------------------------------------------------------------------
# Tool registry
# ----------------------------------------------------------------------------
# Each tool is a Python function plus an Anthropic tool schema. The @tool
# decorator registers both. Set requires_approval=True for anything that
# writes data, moves money, or sends an external message.

_TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, schema: dict, requires_approval: bool = False):
    """Register a function as a tool the agent can call."""
    def wrapper(fn: Callable[..., Any]):
        _TOOLS[name] = {
            "fn": fn,
            "requires_approval": requires_approval,
            "spec": {
                "name": name,
                "description": description,
                "input_schema": schema,
            },
        }
        return fn
    return wrapper


def tool_specs() -> list[dict]:
    """The schemas sent to the API so Claude knows what it can call."""
    return [t["spec"] for t in _TOOLS.values()]


def run_tool(name: str, args: dict) -> str:
    """Execute a registered tool, enforcing the approval guardrail."""
    entry = _TOOLS.get(name)
    if not entry:
        return f"ERROR: unknown tool '{name}'"

    if entry["requires_approval"] and not _approve(name, args):
        return "DENIED: the human declined this action. Do not retry it."

    try:
        result = entry["fn"](**args)
        return result if isinstance(result, str) else json.dumps(result, default=str)
    except Exception as e:  # tool errors go back to the model so it can recover
        return f"ERROR running {name}: {e}"


def _approve(name: str, args: dict) -> bool:
    """Terminal approval prompt for sensitive tools. Swap for Telegram later."""
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
# The core loop
# ----------------------------------------------------------------------------

def run_agent(task: str) -> str:
    """Run the agent until the task is done or MAX_TURNS is hit."""
    messages = [{"role": "user", "content": task}]

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tool_specs(),
            messages=messages,
        )

        # Print any text the model produced this turn (its reasoning / answer).
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[agent] {block.text.strip()}")

        # No tool requested -> the model is done.
        if response.stop_reason != "tool_use":
            final = " ".join(b.text for b in response.content if b.type == "text")
            return final.strip()

        # Otherwise: record the assistant turn, run each requested tool,
        # and send the results back as a single user message.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"[tool] -> {block.name}({json.dumps(block.input, ensure_ascii=False)})")
                output = run_tool(block.name, block.input)
                print(f"[tool] <- {output[:300]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        messages.append({"role": "user", "content": tool_results})

    return "Stopped: reached the maximum number of turns without finishing."


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "What pending tasks do I have, and which is most urgent?"
    print(f"TASK: {task}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first:  export ANTHROPIC_API_KEY=sk-ant-...")
    summary = run_agent(task)
    print(f"\n{'='*60}\nDONE: {summary}")
