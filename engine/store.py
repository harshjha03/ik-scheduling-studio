"""Durable storage for the schedule, the publish log and the calendar event ids we own.

Two drivers, chosen by environment:
  * `DATABASE_URL` set  -> Postgres (Neon/Supabase/RDS). The only option that works on Vercel,
    whose functions have no durable filesystem.
  * otherwise           -> SQLite at IK_DB_PATH (default .data/ik.db). Local development only.

Everything is plain SQL over two placeholder styles, so there is no ORM to learn or migrate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing, nullcontext
from datetime import datetime, timezone

_lock = threading.Lock()          # one process, one lock; per-connection pooling if it ever matters

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS schedule (
        week        TEXT PRIMARY KEY,
        payload     TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS publish_log (
        id          {serial},
        week        TEXT NOT NULL,
        channel     TEXT NOT NULL,
        audience    TEXT NOT NULL,
        status      TEXT NOT NULL,
        detail      TEXT,
        live        {boolean} NOT NULL,
        at          TEXT NOT NULL
    )""",
    # (session_id, calendar_id) — the same class can live on the ops calendar and on a cohort
    # calendar; keying by session alone made the second publish overwrite the first one's event.
    """CREATE TABLE IF NOT EXISTS calendar_event (
        session_id  TEXT NOT NULL,
        calendar_id TEXT NOT NULL,
        event_id    TEXT NOT NULL,
        body_hash   TEXT,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (session_id, calendar_id)
    )""",
    # Every override is a labelled human (or agent) disagreement with the matcher, and the override
    # rate over time is the trust metric. In page state it died on refresh.
    """CREATE TABLE IF NOT EXISTS override_log (
        id          {serial},
        week        TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        batch_id    TEXT NOT NULL,
        from_sme_id TEXT,
        to_sme_id   TEXT NOT NULL,
        actor       TEXT NOT NULL,
        rule_risk   TEXT,
        at          TEXT NOT NULL
    )""",
    # What the app boots from. A row here overrides the bundled data/*.json of the same name, so seed
    # JSON becomes the documented default source rather than the only possible one.
    """CREATE TABLE IF NOT EXISTS dataset (
        name        TEXT PRIMARY KEY,
        payload     TEXT NOT NULL,
        source      TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""",
]

# Added after calendar_event shipped. SQLite cannot ADD COLUMN IF NOT EXISTS, so this runs
# unconditionally and a duplicate-column error is the expected no-op on an existing database.
MIGRATIONS = ["ALTER TABLE calendar_event ADD COLUMN body_hash TEXT"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Minimal SQL store. `driver` is 'postgres' or 'sqlite'."""

    def __init__(self, url: str | None = None, path: str | None = None):
        self.url = url if url is not None else os.environ.get("DATABASE_URL")
        self.driver = "postgres" if self.url else "sqlite"
        self.path = path or os.environ.get("IK_DB_PATH") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".data", "ik.db")
        if self.driver == "sqlite":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._pg = None      # one long-lived Postgres connection per process (see _connect)
        self._init()

    # ---- plumbing ----

    def _connect(self):
        if self.driver == "postgres":
            import psycopg  # imported lazily so local dev needs no driver at all
            # A fresh TCP+TLS handshake per query cost ~2s each from Vercel to a managed Postgres; a
            # publish does two per class row. Keep one connection and reconnect only when the provider
            # has dropped it (Neon suspends idle compute; Supabase's Supavisor drops idle clients).
            if self._pg is None or self._pg.closed:
                # prepare_threshold=None disables psycopg3's automatic server-side prepared statements.
                # Harmless in session mode, and required behind a transaction-mode pooler (Supavisor on
                # 6543), where the next statement may land on a different backend and the prepared name
                # is gone: "prepared statement _pg3_0 does not exist", only once the app is warm.
                self._pg = psycopg.connect(self.url, prepare_threshold=None)
            return self._pg
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _sql(self, q: str) -> str:
        """One statement, two dialects."""
        if self.driver == "postgres":
            return q.replace("?", "%s").format(serial="SERIAL PRIMARY KEY", boolean="BOOLEAN")
        return q.replace("%s", "?").format(serial="INTEGER PRIMARY KEY AUTOINCREMENT", boolean="INTEGER")

    def _run(self, q: str, args: tuple = (), fetch: str | None = None, retry: bool = True):
        # closing() matters for sqlite: its `with conn` commits but never closes, so a long-lived
        # dev server leaked one file handle per query. psycopg closes on exit anyway.
        try:
            with _lock:
                conn = self._connect()
                # sqlite: close after every query (a dev server leaked one handle per query otherwise);
                # postgres: keep it open, `with conn` still commits/rolls back the transaction.
                with (nullcontext(conn) if self.driver == "postgres" else closing(conn)), conn:
                    cur = conn.cursor()
                    cur.execute(self._sql(q), args)
                    return cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else None
        except Exception as exc:
            msg = str(exc)
            # Neon suspends idle compute and Supavisor drops idle clients; either way the socket goes
            # and the next query fails once. Reconnect and retry.
            if retry and self.driver == "postgres" and (self._pg is None or self._pg.closed or
                                                       "connection" in msg.lower() or "SSL" in msg):
                self._pg = None
                return self._run(q, args, fetch, retry=False)
            # The schema is created once per process. If the .db file is deleted, or the database is
            # reset, under a long-lived process, every later query fails forever — rebuild and retry.
            if not (retry and ("no such table" in msg or "does not exist" in msg)):
                raise
            self._init()
            return self._run(q, args, fetch, retry=False)

    def _init(self) -> None:
        for stmt in SCHEMA:
            self._run(stmt, retry=False)
        for stmt in MIGRATIONS:
            try:
                self._run(stmt, retry=False)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower() and "already exists" not in str(exc).lower():
                    raise

    # ---- schedule ----

    def save_schedule(self, week: str, payload: dict) -> str:
        """Returns the new updated_at, which the client echoes back as its version on the next save."""
        blob, stamp = json.dumps(payload), now()
        if self.driver == "postgres":
            self._run("INSERT INTO schedule (week, payload, updated_at) VALUES (?,?,?) "
                      "ON CONFLICT (week) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                      (week, blob, stamp))
        else:
            self._run("INSERT OR REPLACE INTO schedule (week, payload, updated_at) VALUES (?,?,?)", (week, blob, stamp))
        return stamp

    def load_schedule(self, week: str) -> dict | None:
        row = self._run("SELECT payload, updated_at FROM schedule WHERE week = ?", (week,), fetch="one")
        if not row:
            return None
        return {"week": week, "updated_at": row[1], **json.loads(row[0])}

    def weeks(self) -> list[str]:
        return [r[0] for r in (self._run("SELECT week FROM schedule ORDER BY week", fetch="all") or [])]

    # ---- publish log ----

    def record_publish(self, week: str, channel: str, audience: str, status: str, detail: str, live: bool) -> None:
        self._run("INSERT INTO publish_log (week, channel, audience, status, detail, live, at) VALUES (?,?,?,?,?,?,?)",
                  (week, channel, audience, status, detail, bool(live) if self.driver == "postgres" else int(live), now()))

    def publish_log(self, week: str | None = None, limit: int = 50) -> list[dict]:
        if week:
            rows = self._run("SELECT week, channel, audience, status, detail, live, at FROM publish_log "
                             "WHERE week = ? ORDER BY id DESC LIMIT ?", (week, limit), fetch="all")
        else:
            rows = self._run("SELECT week, channel, audience, status, detail, live, at FROM publish_log "
                             "ORDER BY id DESC LIMIT ?", (limit,), fetch="all")
        keys = ("week", "channel", "audience", "status", "detail", "live", "at")
        return [{**dict(zip(keys, r)), "live": bool(r[5])} for r in (rows or [])]

    # ---- override log (the trust metric) ----

    def record_overrides(self, week: str, entries: list[dict], actor: str = "human") -> int:
        """One row per override. `entries` is what apply_approvals already returns, so the pure
        engine stays pure and the route that has a database does the writing."""
        at = now()
        wrote = 0
        for e in entries or []:
            if not e.get("to_sme_id"):
                continue
            self._run("INSERT INTO override_log (week, session_id, batch_id, from_sme_id, to_sme_id, "
                      "actor, rule_risk, at) VALUES (?,?,?,?,?,?,?,?)",
                      (week, e["session_id"], e.get("batch_id") or "", e.get("from_sme_id"),
                       e["to_sme_id"], e.get("actor") or actor, e.get("rule_risk"), at))
            wrote += 1
        return wrote

    def overrides(self, week: str | None = None, limit: int = 100) -> list[dict]:
        keys = ("week", "session_id", "batch_id", "from_sme_id", "to_sme_id", "actor", "rule_risk", "at")
        cols = ", ".join(keys)
        if week:
            rows = self._run(f"SELECT {cols} FROM override_log WHERE week = ? ORDER BY id DESC LIMIT ?",
                             (week, limit), fetch="all")
        else:
            rows = self._run(f"SELECT {cols} FROM override_log ORDER BY id DESC LIMIT ?", (limit,), fetch="all")
        return [dict(zip(keys, r)) for r in (rows or [])]

    def override_counts(self) -> dict[str, int]:
        """week -> how many distinct sessions were overridden in it. Distinct, because overriding the
        same class twice is one disagreement, not two."""
        rows = self._run("SELECT week, COUNT(DISTINCT session_id) FROM override_log GROUP BY week", fetch="all")
        return {r[0]: int(r[1]) for r in (rows or [])}

    # ---- datasets the app boots from ----

    def save_dataset(self, name: str, payload, source: str) -> None:
        blob = json.dumps(payload)
        if self.driver == "postgres":
            self._run("INSERT INTO dataset (name, payload, source, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload, "
                      "source = EXCLUDED.source, updated_at = EXCLUDED.updated_at",
                      (name, blob, source, now()))
        else:
            self._run("INSERT OR REPLACE INTO dataset (name, payload, source, updated_at) VALUES (?,?,?,?)",
                      (name, blob, source, now()))

    def load_datasets(self) -> dict[str, dict]:
        rows = self._run("SELECT name, payload, source, updated_at FROM dataset", fetch="all")
        return {r[0]: {"payload": json.loads(r[1]), "source": r[2], "updated_at": r[3]} for r in (rows or [])}

    def reset_datasets(self) -> int:
        """Back to the bundled seed data. Doubles as demo safety: a reviewer who mangles the roster
        recovers in one click."""
        before = len(self.load_datasets())
        self._run("DELETE FROM dataset")
        return before

    # ---- calendar events we own (so a re-publish updates instead of duplicating) ----

    def remember_event(self, session_id: str, calendar_id: str, event_id: str) -> None:
        if self.driver == "postgres":
            self._run("INSERT INTO calendar_event (session_id, calendar_id, event_id, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT (session_id, calendar_id) DO UPDATE SET "
                      "event_id = EXCLUDED.event_id, updated_at = EXCLUDED.updated_at",
                      (session_id, calendar_id, event_id, now()))
        else:
            self._run("INSERT OR REPLACE INTO calendar_event (session_id, calendar_id, event_id, updated_at) "
                      "VALUES (?,?,?,?)", (session_id, calendar_id, event_id, now()))

    def remember_events(self, calendar_id: str, pairs: list[tuple[str, str, str | None]]) -> None:
        """[(session_id, event_id, body_hash)] in a single round trip.

        41 separate writes cost 41 cross-region round trips on a managed Postgres, and they were
        serialising on the process-wide lock behind a thread pool that had already finished its work.
        """
        if not pairs:
            return
        at = now()
        values = [(calendar_id, sid, eid, h, at) for sid, eid, h in pairs]
        rows = ",".join(["(?,?,?,?,?)"] * len(values))
        args = tuple(x for cal, sid, eid, h, t in values for x in (sid, cal, eid, h, t))
        if self.driver == "postgres":
            self._run(f"INSERT INTO calendar_event (session_id, calendar_id, event_id, body_hash, updated_at) "
                      f"VALUES {rows} ON CONFLICT (session_id, calendar_id) DO UPDATE SET "
                      f"event_id = EXCLUDED.event_id, body_hash = EXCLUDED.body_hash, "
                      f"updated_at = EXCLUDED.updated_at", args)
        else:
            self._run(f"INSERT OR REPLACE INTO calendar_event (session_id, calendar_id, event_id, "
                      f"body_hash, updated_at) VALUES {rows}", args)

    def owned_on(self, calendar_id: str) -> dict[str, tuple[str, str | None]]:
        """{session_id: (event_id, body_hash)} — one query for the whole batch, so a re-publish can
        skip a row whose event body has not changed."""
        rows = self._run("SELECT session_id, event_id, body_hash FROM calendar_event WHERE calendar_id = ?",
                         (calendar_id,), fetch="all")
        return {r[0]: (r[1], r[2]) for r in (rows or [])}

    def events_on(self, calendar_id: str) -> dict[str, str]:
        """{session_id: event_id} for every event we own on one calendar — one query, not one per row."""
        rows = self._run("SELECT session_id, event_id FROM calendar_event WHERE calendar_id = ?",
                         (calendar_id,), fetch="all")
        return {r[0]: r[1] for r in (rows or [])}

    def event_for(self, session_id: str, calendar_id: str) -> str | None:
        """The event id we already own for this class *on this calendar*, if any."""
        row = self._run("SELECT event_id FROM calendar_event WHERE session_id = ? AND calendar_id = ?",
                        (session_id, calendar_id), fetch="one")
        return row[0] if row else None

    def forget_event(self, session_id: str, calendar_id: str) -> None:
        """Called when Google says the event is gone, so the next publish re-creates it."""
        self._run("DELETE FROM calendar_event WHERE session_id = ? AND calendar_id = ?", (session_id, calendar_id))

    def info(self) -> dict:
        return {"driver": self.driver, "location": "postgres" if self.driver == "postgres" else self.path,
                "durable": self.driver == "postgres"}


_store: Store | None = None


def store() -> Store:
    """Process-wide store. Safe to call per request — a warm Vercel instance reuses it."""
    global _store
    if _store is None:
        _store = Store()
    return _store
