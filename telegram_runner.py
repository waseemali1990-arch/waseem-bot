"""
telegram_runner.py — Run the accounting agent from Telegram.

What it does:
  1. Listens for messages. Any text you send becomes a task for the agent.
  2. Replaces the terminal approval prompt with Approve / Deny buttons on your
     phone. The agent blocks until you tap one (or times out and denies).
  3. Sends the final summary back to your chat.

Setup:
    pip install requests openpyxl anthropic
    export ANTHROPIC_API_KEY="sk-ant-..."
    export TELEGRAM_BOT_TOKEN="123456:ABC..."     # from @BotFather
    export TELEGRAM_CHAT_ID="987654321"           # your chat id (only this chat is trusted)
    python telegram_runner.py

Then just message your bot: "Which tasks are due before 2026-07-12?"
"""

import os
import time
import secrets
import requests
from typing import Optional

import agent_core
import tools_excel          # Excel: read, search, list files
import tools_files          # PDF, Word, send files, calculate, find files

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

APPROVAL_TIMEOUT = 300      # seconds to wait for a tap before defaulting to DENY
_offset = None             # shared getUpdates cursor so the two pollers don't eat
                            # each other's updates


# ----------------------------------------------------------------------------
# Thin Telegram Bot API helpers
# ----------------------------------------------------------------------------

def send(text: str, buttons: Optional[list] = None) -> int:
    payload = {"chat_id": CHAT_ID, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=30).json()
    return r["result"]["message_id"]


def get_updates(timeout: int = 25) -> list:
    global _offset
    params = {"timeout": timeout}
    if _offset is not None:
        params["offset"] = _offset
    r = requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 10).json()
    updates = r.get("result", [])
    if updates:
        _offset = updates[-1]["update_id"] + 1   # advance past what we've seen
    return updates


def answer_callback(callback_id: str):
    requests.post(f"{API}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=15)


# ----------------------------------------------------------------------------
# Tap-to-approve  (overrides agent_core._approve)
# ----------------------------------------------------------------------------

def telegram_approve(name: str, args: dict) -> bool:
    """Send an Approve/Deny prompt, block until tapped or timed out."""
    token = secrets.token_hex(4)
    send(
        f"\u26a0\ufe0f Approval needed\n\n{name}\n{args}",
        buttons=[[
            {"text": "\u2705 Approve", "callback_data": f"ok:{token}"},
            {"text": "\u274c Deny",    "callback_data": f"no:{token}"},
        ]],
    )

    deadline = time.time() + APPROVAL_TIMEOUT
    while time.time() < deadline:
        for u in get_updates():
            cb = u.get("callback_query")
            if cb and cb.get("data", "").endswith(token):
                answer_callback(cb["id"])
                approved = cb["data"].startswith("ok:")
                send("Approved \u2705" if approved else "Denied \u274c")
                return approved
        time.sleep(1)

    send("\u23f1\ufe0f Approval timed out \u2014 denied for safety.")
    return False


agent_core._approve = telegram_approve   # swap terminal prompt for phone taps


# ----------------------------------------------------------------------------
# Main intake loop
# ----------------------------------------------------------------------------

WASEEM_DATA = "/Users/waseemali/Library/CloudStorage/OneDrive-AbdullahTurkeyAlduhayansonsforconstruction/Waseem Data"
INBOX = os.path.join(WASEEM_DATA, "Inbox")


def download_and_save(msg: dict) -> Optional[str]:
    """Download any file/photo the user sends and save it to Waseem Data/Inbox."""
    os.makedirs(INBOX, exist_ok=True)

    file_id = None
    file_name = None

    if "document" in msg:
        file_id   = msg["document"]["file_id"]
        file_name = msg["document"].get("file_name", f"document_{file_id}")
    elif "photo" in msg:
        file_id   = msg["photo"][-1]["file_id"]  # largest size
        file_name = f"photo_{file_id}.jpg"
    elif "video" in msg:
        file_id   = msg["video"]["file_id"]
        file_name = msg["video"].get("file_name", f"video_{file_id}.mp4")
    elif "audio" in msg:
        file_id   = msg["audio"]["file_id"]
        file_name = msg["audio"].get("file_name", f"audio_{file_id}.mp3")
    elif "voice" in msg:
        file_id   = msg["voice"]["file_id"]
        file_name = f"voice_{file_id}.ogg"

    if not file_id:
        return None

    # Get file path from Telegram
    r = requests.get(f"{API}/getFile", params={"file_id": file_id}, timeout=15).json()
    if not r.get("ok"):
        return f"ERROR: could not get file info \u2014 {r.get('description')}"

    tg_path = r["result"]["file_path"]
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{tg_path}"

    # Download
    resp = requests.get(download_url, timeout=60)
    save_path = os.path.join(INBOX, file_name)

    # Avoid overwriting \u2014 add a counter if file exists
    base, ext = os.path.splitext(save_path)
    counter = 1
    while os.path.exists(save_path):
        save_path = f"{base}_{counter}{ext}"
        counter += 1

    with open(save_path, "wb") as f:
        f.write(resp.content)

    rel = os.path.relpath(save_path, WASEEM_DATA)
    return rel


def main():
    send("\U0001f916 Accounting agent online. Send me a task or send any file to save it.")
    print("Listening for Telegram messages... (Ctrl+C to stop)")

    while True:
        try:
            for u in get_updates():
                msg = u.get("message")
                if not msg:
                    continue
                if str(msg["chat"]["id"]) != CHAT_ID:
                    continue

                # Handle file/photo uploads
                if any(k in msg for k in ("document", "photo", "video", "audio", "voice")):
                    caption = msg.get("caption", "").strip()
                    saved = download_and_save(msg)
                    if saved and not saved.startswith("ERROR"):
                        reply = f"\u2705 Saved to Waseem Data/{saved}"
                        if caption:
                            # User added a caption \u2014 let agent handle it as context
                            try:
                                summary = agent_core.run_agent(f"File saved at {saved}. User note: {caption}")
                                reply += f"\n\n{summary}"
                            except Exception:
                                pass
                    else:
                        reply = saved or "\u274c Could not save file."
                    send(reply)
                    continue

                if "text" not in msg:
                    continue

                task = msg["text"].strip()
                if task.lower() in ("/start", "/help"):
                    send("Send me any task or file:\n"
                         "\u2022 Which tasks are due this week?\n"
                         "\u2022 Send me the latest cash report\n"
                         "\u2022 \ud83d\udcce Send any PDF/Excel/image to save it to your OneDrive")
                    continue

                send(f"Working on: {task}")
                try:
                    summary = agent_core.run_agent(task)
                except Exception as e:
                    summary = f"Error: {e}"
                send(f"\u2705 Done\n\n{summary}" if summary else "Done.")

        except KeyboardInterrupt:
            send("\U0001f916 Agent stopped.")
            break
        except Exception as e:
            print(f"loop error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
