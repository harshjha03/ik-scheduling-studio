"""Seed an existing Google Sheet for the pull/push paths, as the service account.

    .venv/bin/python scripts/sheets_seed.py <spreadsheet id or URL>

Create the sheet yourself and share it (Editor) with the bot's client_email first: since 2025 service
accounts have no Drive storage of their own, so `spreadsheets.create` returns 403 for them.
Tabs (added if missing): Sessions (a NEW batch, so Pull does not collide with the seeded week), SMEs (two new teachers),
History (the seed history in template form), Draft (empty; Push fills it). Prints SHEET_ID.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine import dotenv  # noqa: E402

dotenv.load(os.path.join(ROOT, ".env"))
os.environ.pop("GOOGLE_OAUTH_JSON", None)          # the bot, not the person
os.environ.setdefault("DATABASE_URL", "")
from engine import channels as C  # noqa: E402

def call(method, url, tok, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def rd(name):
    with open(os.path.join(ROOT, "data", f"{name}.json")) as f:
        return json.load(f)


def main() -> int:
    share = sys.argv[1] if len(sys.argv) > 1 else None
    courses, history, smes = rd("courses"), rd("history"), rd("smes")
    topics = courses["DSA"]["topics"]
    sessions = [["batch_id", "course", "level", "learners", "topic", "class_type", "day", "time", "sme_name"],
                ["DSA-09", "DSA", "intermediate", 28, topics[0], "class", "Tue", "11:00", ""],
                ["DSA-09", "DSA", "intermediate", 28, topics[1], "class", "Thu", "11:00", ""],
                ["DSA-09", "DSA", "intermediate", 28, topics[0], "doubt", "Sat", "10:00", ""]]
    sme_rows = [["sme_id", "name", "email", "phone", "city", "courses", "topics", "level", "preferred_per_week", "work_days", "work_hours"],
                ["", "Nikhil Raman", "nikhil.raman@ik.example", "+91 98100 11223", "Bengaluru", "DSA", "|".join(topics[:2]), "intermediate", 4, "Mon-Fri", "09:00-18:00"],
                ["", "Leena Fernandes", "leena.fernandes@ik.example", "+91 98670 44556", "Goa", "ML|AI", "ML Coding|RAG & Retrieval", "beginner", 3, "Tue|Thu|Sat", "14:00-20:00"]]
    known = {t for c in courses.values() for t in c["topics"]}
    hist_rows = [["sme_id", "week", "sessions_taught", "batches", "per_topic_rating"]] + [
        [h["sme_id"], h["week"], h["sessions_taught"], "|".join(h.get("batches") or []),
         "|".join(f"{t}:{v}" for t, v in (h.get("per_topic_rating") or {}).items() if t in known)]
        for h in history]

    tok = C._google_token([C.SHEETS_SCOPE])
    sid = share.rsplit("/d/", 1)[-1].split("/")[0] if share else ""
    if not sid:
        print(__doc__); return 1
    ss = call("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{sid}?fields=spreadsheetUrl,sheets.properties.title", tok)
    have = {sh["properties"]["title"] for sh in ss["sheets"]}
    missing = [t for t in ("Sessions", "SMEs", "History", "Draft") if t not in have]
    if missing:
        call("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate", tok,
             {"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]})
    call("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values:batchUpdate", tok,
         {"valueInputOption": "RAW", "data": [{"range": "Sessions!A1", "values": sessions},
                                              {"range": "SMEs!A1", "values": sme_rows},
                                              {"range": "History!A1", "values": hist_rows}]})
    print("url:", ss["spreadsheetUrl"], "· tabs added:", missing or "none")
    print("rows: sessions", len(sessions) - 1, "· smes", len(sme_rows) - 1, "· history", len(hist_rows) - 1)
    print(f"SHEET_ID={sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
