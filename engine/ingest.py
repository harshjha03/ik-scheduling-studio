"""One ingest path, two backends.

The app reads its week from a `Source`. `SeedSource` serves the bundled `data/*.json` and needs no
credentials at all; `SheetSource` reads a Google Sheet tab. Both answer with **CSV text**, which the
frontend runs through `lib/import.ts` — the single validator. So Sheets is not bolted onto the side
of CSV: it is one of two sources behind the same contract, and the seed data is the documented
default rather than "static data we could not replace".

The seed serialisers below are *writers*, not parsers: one way, no validation, no interpretation of
what a good row looks like. All of that judgement stays in `lib/import.ts`, so these cannot drift
into being a second implementation of the contract.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta

from . import channels as C
from . import sheets as SH
from . import stages as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# dataset -> (sheet tab, the seed json it falls back to)
DATASETS = {
    "sessions": ("Sessions", "sessions_next"),
    "smes": ("SMEs", "smes"),
    "history": ("History", "history"),
}
# The three import contracts, from lib/import.ts. Header order only — the parser matches by name.
CLASS_COLUMNS = ("batch_id", "course", "level", "learners", "topic", "class_type", "day", "time", "sme_name")
SME_COLUMNS = ("sme_id", "name", "email", "phone", "city", "courses", "topics", "level",
               "preferred_per_week", "work_days", "work_hours")
HISTORY_COLUMNS = ("sme_id", "week", "sessions_taught", "batches", "per_topic_rating")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def seed(name: str) -> list | dict:
    with open(os.path.join(DATA, f"{name}.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------- seed -> CSV (one way, no rules)

def _ist(row: dict):
    return S.parse_utc(row["start_utc"]).astimezone(S.IST)


def sessions_csv(sessions: list[dict], batches: list[dict], smes: list[dict]) -> str:
    by_batch = {b["id"]: b for b in batches}
    by_sme = {s["id"]: s for s in smes}
    rows = [list(CLASS_COLUMNS)]
    for x in sorted(sessions, key=lambda r: (r["start_utc"], r["id"])):
        b = by_batch.get(x["batch_id"], {})
        at = _ist(x)
        rows.append([
            x["batch_id"], x["subject"], b.get("level", ""), b.get("learners", ""),
            x.get("sub_specialty") or "", x["type"], DAYS[at.weekday()], f"{at.hour:02d}:00",
            by_sme.get(x.get("sme_id") or "", {}).get("name", ""),
        ])
    return SH.to_csv(rows)


def smes_csv(smes: list[dict]) -> str:
    rows = [list(SME_COLUMNS)]
    for s in smes:
        windows = s.get("weekly_availability") or []
        days = "|".join(sorted({w["weekday"] for w in windows}, key=DAYS.index)) or "Mon-Fri"
        first = windows[0] if windows else {"start_utc": "03:30", "end_utc": "12:30"}
        rows.append([
            s["id"], s["name"], s.get("email") or "", s.get("phone") or "", s.get("city") or "",
            "|".join(S.sme_subjects(s)), "|".join(S.sme_topics(s)), s.get("level") or "",
            s.get("preferred") or 4, days, f"{_to_ist_hhmm(first['start_utc'])}-{_to_ist_hhmm(first['end_utc'])}",
        ])
    return SH.to_csv(rows)


def _to_ist_hhmm(hhmm: str) -> str:
    """The roster contract is in IST hours; the engine stores UTC windows."""
    h, m = (int(x) for x in hhmm.split(":"))
    total = (h * 60 + m + 330) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def history_csv(history: list[dict]) -> str:
    rows = [list(HISTORY_COLUMNS)]
    for h in history:
        ratings = "|".join(f"{k}:{v}" for k, v in (h.get("per_topic_rating") or {}).items())
        rows.append([h["sme_id"], h["week"], h.get("sessions_taught", 0),
                     "|".join(h.get("batches") or []), ratings])
    return SH.to_csv(rows)


# ---------------------------------------------------------------- the sources

class Source:
    """`fetch(dataset) -> Result` with a `csv` field. Everything downstream is identical per source."""

    name = "source"

    def fetch(self, dataset: str) -> C.Result:      # pragma: no cover - interface
        raise NotImplementedError


class SeedSource(Source):
    """The bundled week. Always available, needs nothing configured — this is what a reviewer with
    no Google account sees, and it is the default rather than a fallback of last resort."""

    name = "seed data"

    def fetch(self, dataset: str) -> C.Result:
        if dataset not in DATASETS:
            return C.Result("error", f"Unknown dataset `{dataset}`.", 0, csv="", source=self.name)
        if dataset == "sessions":
            text = sessions_csv(seed("sessions_next"), seed("batches"), seed("smes"))
        elif dataset == "smes":
            text = smes_csv(seed("smes"))
        else:
            text = history_csv(seed("history"))
        n = max(0, text.count("\r\n") - 1)
        return C.Result("sent", f"{n} row(s) read from the bundled seed data.", n, False,
                        csv=text, source=self.name, tab=DATASETS[dataset][0])


class SheetSource(Source):
    """A Google Sheet tab, via engine/sheets.py. Reports `simulated` when unconfigured."""

    name = "Google Sheet"

    def __init__(self, spreadsheet_id: str | None = None, tabs: dict | None = None):
        self.spreadsheet_id = spreadsheet_id
        self.tabs = tabs or {}

    def fetch(self, dataset: str) -> C.Result:
        if dataset not in DATASETS:
            return C.Result("error", f"Unknown dataset `{dataset}`.", 0, csv="", source=self.name)
        tab = self.tabs.get(dataset) or DATASETS[dataset][0]
        res = SH.read_tab(self.spreadsheet_id, tab)
        res["source"] = self.name
        return res


def pick_source(spreadsheet_id: str | None = None, prefer: str | None = None) -> Source:
    """Configuration decides, not the caller: a configured Sheet wins, seed data is the default.
    `prefer='seed'` forces the bundled data, which is what the demo path uses."""
    if prefer == "seed":
        return SeedSource()
    ready, _ = SH.sheets_ready(spreadsheet_id)
    return SheetSource(spreadsheet_id) if ready else SeedSource()
