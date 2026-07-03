"""
tools_onedrive.py — Read/write files via Microsoft Graph API (OneDrive).
Used when the bot runs on Railway (cloud) and cannot access local OneDrive sync.

Setup:
1. Go to portal.azure.com → App registrations → New registration
2. Name: WaseemBot, Supported account types: Personal Microsoft accounts
3. After creating: copy Application (client) ID → AZURE_CLIENT_ID
4. Certificates & secrets → New client secret → copy value → AZURE_CLIENT_SECRET
5. API permissions → Add → Microsoft Graph → Delegated → Files.ReadWrite.All
6. Set env vars on Railway:
   AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, ONEDRIVE_REFRESH_TOKEN
"""

import os
import requests
from agent_core import tool

CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
TENANT_ID     = os.environ.get("AZURE_TENANT_ID", "consumers")
REFRESH_TOKEN = os.environ.get("ONEDRIVE_REFRESH_TOKEN", "")

GRAPH = "https://graph.microsoft.com/v1.0"
WASEEM_FOLDER = "Waseem Data"

_access_token = None


def _get_token() -> str:
    global _access_token
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "scope":         "Files.ReadWrite.All offline_access",
        },
        timeout=15,
    ).json()
    _access_token = r.get("access_token")
    return _access_token


def _headers():
    return {"Authorization": f"Bearer {_get_token()}"}


def _is_cloud():
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)


@tool(
    name="onedrive_list_files",
    description="List files in a OneDrive folder (cloud mode). Use when running on Railway.",
    schema={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Subfolder path inside Waseem Data, e.g. 'Cash Report'"},
        },
        "required": [],
    },
)
def onedrive_list_files(folder: str = ""):
    if not _is_cloud():
        return "OneDrive API not configured — running in local mode."
    path = f"{WASEEM_FOLDER}/{folder}".rstrip("/")
    r = requests.get(
        f"{GRAPH}/me/drive/root:/{path}:/children",
        headers=_headers(),
        timeout=15,
    ).json()
    items = r.get("value", [])
    return [{"name": i["name"], "type": "folder" if "folder" in i else "file", "size": i.get("size")} for i in items]


@tool(
    name="onedrive_download",
    description="Download and read a file from OneDrive (cloud mode).",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path inside Waseem Data"},
        },
        "required": ["path"],
    },
)
def onedrive_download(path: str):
    if not _is_cloud():
        return "OneDrive API not configured — running in local mode."
    full_path = f"{WASEEM_FOLDER}/{path}"
    r = requests.get(
        f"{GRAPH}/me/drive/root:/{full_path}",
        headers=_headers(),
        timeout=15,
    ).json()
    download_url = r.get("@microsoft.graph.downloadUrl")
    if not download_url:
        return f"ERROR: {r.get('error', {}).get('message', 'File not found')}"
    content = requests.get(download_url, timeout=30)
    return f"Downloaded {path} ({len(content.content)} bytes)"


@tool(
    name="onedrive_upload",
    description="Upload/save a file to OneDrive (cloud mode).",
    schema={
        "type": "object",
        "properties": {
            "path":    {"type": "string", "description": "Destination path inside Waseem Data"},
            "content": {"type": "string", "description": "Text content to save"},
        },
        "required": ["path", "content"],
    },
)
def onedrive_upload(path: str, content: str):
    if not _is_cloud():
        return "OneDrive API not configured — running in local mode."
    full_path = f"{WASEEM_FOLDER}/{path}"
    r = requests.put(
        f"{GRAPH}/me/drive/root:/{full_path}:/content",
        headers={**_headers(), "Content-Type": "text/plain"},
        data=content.encode(),
        timeout=30,
    ).json()
    if "id" in r:
        return f"Saved to OneDrive: {path}"
    return f"ERROR: {r.get('error', {}).get('message', 'Upload failed')}"
