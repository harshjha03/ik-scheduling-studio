"""Google Sheets, both directions — deliberately the dumbest reader that could work.

`lib/import.ts` already owns the column contract for classes, the roster and history: the header
checks, the row-level error wording, the context-aware rules (an import may not double-book a batch
or a teacher, and a named SME must actually be free at that hour) and the templates. A second parser
here, in a second language, would drift from that one within a week and be the worse of the two.

So this module reads cells and serialises them straight back to CSV text, quoting exactly as
`lib/export.ts::toCsv` does, and hands that to the frontend — which feeds it through the same
`parseClassImport` / `parseSmeImport` and the same preview-and-confirm sheet a file upload goes
through. One contract, one validator, one place errors are worded.

Writes go the other way: the approved week's export rows, in `EXPORT_COLUMNS` order, so the CSV
download and the Sheets push are the same shape.
"""
from __future__ import annotations

import os
import urllib.parse

from . import channels as C

# Verbatim from lib/export.ts::EXPORT_COLUMNS. test_sheets_columns_match_the_typescript_contract
# reads that file and asserts these agree, so the two cannot drift silently.
EXPORT_COLUMNS = ("week", "date", "time_ist", "batch", "subject", "sub_specialty",
                  "session_type", "sme_name", "status", "flags")

SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DEFAULT_TABS = {"sessions": "Sessions", "smes": "SMEs", "history": "History", "draft": "Draft"}


def sheet_id(explicit: str | None = None) -> str:
    return (explicit or os.environ.get("SHEET_ID") or "").strip()


def sheets_ready(spreadsheet_id: str | None = None) -> tuple[bool, str]:
    """Mirrors channels.google_ready(): the same kill switch, the same plain-language reasons."""
    if off := C._disabled():
        return False, off
    if not (os.environ.get("GOOGLE_OAUTH_JSON") or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")):
        return False, "GOOGLE_OAUTH_JSON or GOOGLE_SERVICE_ACCOUNT_JSON not set"
    if not sheet_id(spreadsheet_id):
        return False, "SHEET_ID not set (the spreadsheet to read sessions and the roster from)"
    who = "user account" if C.publishes_as_user() else "service account"
    return True, f"{who}, reads tabs and writes the draft"


def status() -> dict:
    ok, why = sheets_ready()
    return {"live": ok, "detail": why, "name": "Google Sheets", "spreadsheet_id": sheet_id() or None,
            "tabs": DEFAULT_TABS}


def _cell(value) -> str:
    """One CSV cell, quoted exactly as lib/export.ts::csvCell quotes it. A leading = @ + or - would be
    evaluated as a formula by Excel or Sheets, so it is neutralised with an apostrophe (QA-08)."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "@", "+", "-"):
        s = "'" + s
    return '"' + s.replace('"', '""') + '"' if any(c in s for c in '",\n\r') else s


def to_csv(rows: list[list]) -> str:
    """Rows of cells to CSV text. `lib/import.ts::splitCsv` is the reader on the other side."""
    return "".join(",".join(_cell(c) for c in row) + "\r\n" for row in rows)


def _api_for(spreadsheet_id: str):
    """`api(method, path, body) -> dict`, injectable so tests never touch the network."""
    token = C._google_token([C.SHEETS_SCOPE])
    base = f"{SHEETS_BASE}/{urllib.parse.quote(spreadsheet_id)}"
    auth = {"Authorization": f"Bearer {token}"}

    def api(method: str, path: str, body: dict | None = None) -> dict:
        url = base + path
        if method == "GET":
            return C._get(url, auth)
        return C._post(url, body or {}, {**auth, "Content-Type": "application/json"}, method)
    return api


def read_tab(spreadsheet_id: str | None, tab_name: str, api=None) -> C.Result:
    """One tab's cells as CSV text. No validation, no interpretation — that lives in lib/import.ts."""
    sid = sheet_id(spreadsheet_id)
    ready, why = sheets_ready(sid)
    if not ready:
        return C.Result("simulated", f"Not read — {why}.", 0, csv="", tab=tab_name)
    api = api or _api_for(sid)
    try:
        got = api("GET", f"/values/{urllib.parse.quote(tab_name)}")
    except Exception as exc:
        return C.Result("error", f"Could not read `{tab_name}` ({type(exc).__name__}).", 0, True,
                        csv="", tab=tab_name)
    rows = [r for r in (got.get("values") or []) if any(str(c).strip() for c in r)]
    if not rows:
        return C.Result("skipped", f"Tab `{tab_name}` is empty.", 0, True, csv="", tab=tab_name)
    return C.Result("sent", f"{len(rows) - 1} row(s) read from `{tab_name}`.", len(rows) - 1, True,
                    csv=to_csv(rows), tab=tab_name)


def write_draft(spreadsheet_id: str | None, rows: list[dict], tab_name: str | None = None, api=None) -> C.Result:
    """The approved week into a tab, in EXPORT_COLUMNS order. The range is cleared first so a shorter
    week cannot leave last week's tail behind."""
    sid = sheet_id(spreadsheet_id)
    tab = tab_name or DEFAULT_TABS["draft"]
    ready, why = sheets_ready(sid)
    if not ready:
        return C.Result("simulated", f"Not written — {why}.", len(rows), tab=tab)
    api = api or _api_for(sid)
    values = [list(EXPORT_COLUMNS)] + [[str(r.get(c, "") or "") for c in EXPORT_COLUMNS] for r in rows]
    quoted = urllib.parse.quote(tab)
    try:
        api("POST", f"/values/{quoted}:clear", {})
        api("PUT", f"/values/{quoted}!A1?valueInputOption=RAW", {"values": values})
    except Exception as exc:
        return C.Result("error", f"Sheets rejected the write ({type(exc).__name__}).", 0, True, tab=tab)
    return C.Result("sent", f"{len(rows)} row(s) written to `{tab}`.", len(rows), True, tab=tab)
