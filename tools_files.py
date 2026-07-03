"""
tools_files.py — All-rounder file tools: send files, read PDFs, read Word docs,
                 do calculations, search everything in Waseem Data.
"""

import os
import glob
import json
import requests
import fitz          # PyMuPDF
import docx

from agent_core import tool, BOT_TOKEN, CHAT_ID

WASEEM_DATA = "/Users/waseemali/Library/CloudStorage/OneDrive-AbdullahTurkeyAlduhayansonsforconstruction/Waseem Data"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Tool 1 — send any file as a Telegram attachment
# ---------------------------------------------------------------------------

@tool(
    name="send_file",
    description=(
        "Send any file from Waseem Data to the user as a Telegram attachment. "
        "Use when the user asks to 'send', 'share', or 'attach' a file."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path inside Waseem Data, e.g. 'Cash Report/All Companies Bank Balance 10-6-2026.xlsx'"},
            "caption": {"type": "string", "description": "Optional caption to send with the file"},
        },
        "required": ["path"],
    },
)
def send_file(path: str, caption: str = ""):
    full = os.path.join(WASEEM_DATA, path)
    if not os.path.exists(full):
        # try fuzzy match
        pattern = os.path.join(WASEEM_DATA, "**", f"*{os.path.basename(path)}*")
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            return f"ERROR: file not found — {path}"
        full = matches[0]
        path = os.path.relpath(full, WASEEM_DATA)

    size_mb = os.path.getsize(full) / 1_048_576
    if size_mb > 49:
        return f"ERROR: file is {size_mb:.1f} MB — Telegram limit is 50 MB."

    with open(full, "rb") as f:
        r = requests.post(
            f"{API}/sendDocument",
            data={"chat_id": CHAT_ID, "caption": caption or path},
            files={"document": (os.path.basename(full), f)},
            timeout=60,
        ).json()

    if r.get("ok"):
        return f"Sent: {path}"
    return f"ERROR sending file: {r.get('description')}"


# ---------------------------------------------------------------------------
# Tool 2 — find a file by partial name
# ---------------------------------------------------------------------------

@tool(
    name="find_file",
    description="Find files in Waseem Data by partial name. Returns matching paths.",
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Partial filename to search for, e.g. 'IBAN' or 'cash report' or '3CV'"},
        },
        "required": ["name"],
    },
)
def find_file(name: str):
    pattern = os.path.join(WASEEM_DATA, "**", f"*{name}*")
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return f"No files found matching '{name}'"
    return [os.path.relpath(m, WASEEM_DATA) for m in sorted(matches)[:30]]


# ---------------------------------------------------------------------------
# Tool 3 — read a PDF file
# ---------------------------------------------------------------------------

@tool(
    name="read_pdf",
    description=(
        "Extract and read text from a PDF file in Waseem Data. "
        "Use for bank statements, certificates, forms, IBAN docs, audit reports, etc."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the PDF inside Waseem Data"},
            "pages": {"type": "string", "description": "Page range to read, e.g. '1-3' or '1' (default: all, max 10 pages)"},
        },
        "required": ["path"],
    },
)
def read_pdf(path: str, pages: str = None):
    full = os.path.join(WASEEM_DATA, path)
    if not os.path.exists(full):
        matches = glob.glob(os.path.join(WASEEM_DATA, "**", f"*{os.path.basename(path)}*"), recursive=True)
        if not matches:
            return f"ERROR: PDF not found — {path}"
        full = matches[0]

    doc = fitz.open(full)
    total = len(doc)

    if pages:
        parts = pages.split("-")
        start = int(parts[0]) - 1
        end = int(parts[-1]) if len(parts) > 1 else start + 1
    else:
        start, end = 0, min(10, total)

    text_parts = []
    for i in range(start, min(end, total)):
        text_parts.append(f"--- Page {i+1} ---\n{doc[i].get_text()}")

    doc.close()
    full_text = "\n".join(text_parts).strip()
    if not full_text:
        return "PDF appears to be scanned images — no extractable text found."
    return full_text[:8000]  # cap at 8k chars


# ---------------------------------------------------------------------------
# Tool 4 — read a Word document
# ---------------------------------------------------------------------------

@tool(
    name="read_word",
    description="Read text from a Word (.docx) file in Waseem Data.",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the .docx file inside Waseem Data"},
        },
        "required": ["path"],
    },
)
def read_word(path: str):
    full = os.path.join(WASEEM_DATA, path)
    if not os.path.exists(full):
        matches = glob.glob(os.path.join(WASEEM_DATA, "**", f"*{os.path.basename(path)}*"), recursive=True)
        if not matches:
            return f"ERROR: Word file not found — {path}"
        full = matches[0]

    d = docx.Document(full)
    text = "\n".join(p.text for p in d.paragraphs if p.text.strip())
    return text[:8000] if text else "No text found in document."


# ---------------------------------------------------------------------------
# Tool 5 — calculate / run a formula
# ---------------------------------------------------------------------------

@tool(
    name="calculate",
    description=(
        "Perform financial calculations: percentages, VAT (15%), profit/loss, "
        "currency conversion, loan amortization, returns, ratios. "
        "Pass a plain-English expression or formula."
    ),
    schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. '1500000 * 0.15' or 'VAT on 250000' or '5% of 3200000'"},
        },
        "required": ["expression"],
    },
)
def calculate(expression: str):
    expr = expression.lower().strip()

    # Handle plain-English VAT
    import re
    vat_match = re.search(r"vat on ([\d,]+)", expr)
    pct_match = re.search(r"([\d.]+)%\s*of\s*([\d,]+)", expr)

    if vat_match:
        amount = float(vat_match.group(1).replace(",", ""))
        vat = amount * 0.15
        total = amount + vat
        return {"base": amount, "VAT_15%": vat, "total_with_VAT": total}

    if pct_match:
        pct = float(pct_match.group(1)) / 100
        amount = float(pct_match.group(2).replace(",", ""))
        return {"result": pct * amount}

    # Safe eval for numeric expressions
    safe_expr = re.sub(r"[^0-9+\-*/().\s]", "", expression)
    try:
        result = eval(safe_expr, {"__builtins__": {}})
        return {"result": result}
    except Exception as e:
        return f"Could not calculate: {e}. Please rephrase."


# ---------------------------------------------------------------------------
# Tool 6 — list all file types (PDFs, Word, Excel) in a folder
# ---------------------------------------------------------------------------

@tool(
    name="list_folder",
    description="List all files (Excel, PDF, Word) inside a specific subfolder of Waseem Data.",
    schema={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Subfolder name, e.g. 'Cash Report' or 'co. work' or 'Task'"},
        },
        "required": ["folder"],
    },
)
def list_folder(folder: str):
    base = os.path.join(WASEEM_DATA, folder)
    if not os.path.exists(base):
        # fuzzy
        candidates = [d for d in os.listdir(WASEEM_DATA) if folder.lower() in d.lower()]
        if not candidates:
            return f"Folder '{folder}' not found in Waseem Data."
        base = os.path.join(WASEEM_DATA, candidates[0])

    files = []
    for ext in ("*.xlsx", "*.xls", "*.pdf", "*.docx", "*.doc"):
        files += glob.glob(os.path.join(base, "**", ext), recursive=True)

    if not files:
        return f"No Excel/PDF/Word files found in {folder}"

    return [os.path.relpath(f, WASEEM_DATA) for f in sorted(files)[:50]]
