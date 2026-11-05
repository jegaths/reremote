"""SQLite storage for remotes, buttons and the raw capture log."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS remotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    brand       TEXT,
    model       TEXT,
    technology  TEXT NOT NULL DEFAULT 'rf433',
    notes       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buttons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_id     INTEGER NOT NULL REFERENCES remotes(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    protocol      TEXT,              -- e.g. 'rcswitch-1' or 'raw'
    code          INTEGER,           -- decoded value when protocol is known
    bit_length    INTEGER,
    pulse_length  INTEGER,           -- base pulse in microseconds when known
    timings       TEXT NOT NULL,     -- JSON array of microsecond durations
    fingerprint   TEXT NOT NULL,
    repeats       INTEGER NOT NULL DEFAULT 1,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(remote_id, name)
);

CREATE TABLE IF NOT EXISTS captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at  TEXT NOT NULL,
    protocol     TEXT,
    code         INTEGER,
    bit_length   INTEGER,
    pulse_length INTEGER,
    timings      TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    repeats      INTEGER NOT NULL,
    button_id    INTEGER REFERENCES buttons(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS button_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    button_id   INTEGER NOT NULL REFERENCES buttons(id) ON DELETE CASCADE,
    code        INTEGER,
    fingerprint TEXT NOT NULL,
    timings     TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_buttons_fp ON buttons(fingerprint);
CREATE INDEX IF NOT EXISTS idx_samples_btn ON button_samples(button_id);
CREATE INDEX IF NOT EXISTS idx_captures_at ON captures(captured_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._conn:
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                """INSERT INTO button_samples(button_id, code, fingerprint, timings, captured_at)
                   SELECT b.id, b.code, b.fingerprint, b.timings, b.created_at FROM buttons b
                   WHERE NOT EXISTS (SELECT 1 FROM button_samples s WHERE s.button_id = b.id)""")

    # ---- helpers -----------------------------------------------------------
    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [self._decode(dict(r)) for r in cur.fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict[str, Any]]:
        rows = self._rows(sql, args)
        return rows[0] if rows else None

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        if isinstance(row.get("timings"), str):
            row["timings"] = json.loads(row["timings"])
        return row

    # ---- remotes -----------------------------------------------------------
    def list_remotes(self) -> list[dict[str, Any]]:
        return self._rows(
            """SELECT r.*, COUNT(b.id) AS button_count
               FROM remotes r LEFT JOIN buttons b ON b.remote_id = r.id
               GROUP BY r.id ORDER BY r.name"""
        )

    def get_remote(self, remote_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM remotes WHERE id = ?", (remote_id,))

    def create_remote(self, name: str, brand: str = "", model: str = "",
                      technology: str = "rf433", notes: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO remotes(name, brand, model, technology, notes, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (name.strip(), brand.strip(), model.strip(), technology, notes, now_iso()),
            )
            rid = cur.lastrowid
        return self.get_remote(rid)  # type: ignore[return-value]

    def update_remote(self, remote_id: int, **fields: Any) -> Optional[dict[str, Any]]:
        allowed = {"name", "brand", "model", "technology", "notes"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            with self._lock, self._conn:
                self._conn.execute(
                    f"UPDATE remotes SET {cols} WHERE id = ?", (*sets.values(), remote_id)
                )
        return self.get_remote(remote_id)

    def delete_remote(self, remote_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM remotes WHERE id = ?", (remote_id,))
            return cur.rowcount > 0

    # ---- buttons -----------------------------------------------------------
    _BTN_SELECT = """SELECT b.*, r.name AS remote_name,
               (SELECT COUNT(*) FROM button_samples s WHERE s.button_id = b.id) AS sample_count
               FROM buttons b JOIN remotes r ON r.id = b.remote_id"""

    def list_buttons(self, remote_id: Optional[int] = None) -> list[dict[str, Any]]:
        if remote_id is None:
            return self._rows(self._BTN_SELECT + " ORDER BY r.name, b.name")
        return self._rows(self._BTN_SELECT + " WHERE b.remote_id = ? ORDER BY b.name", (remote_id,))

    def get_button(self, button_id: int) -> Optional[dict[str, Any]]:
        return self._one(self._BTN_SELECT + " WHERE b.id = ?", (button_id,))

    def create_button(self, remote_id: int, name: str, timings: list[int],
                      fingerprint: str, protocol: Optional[str] = None,
                      code: Optional[int] = None, bit_length: Optional[int] = None,
                      pulse_length: Optional[int] = None, repeats: int = 1,
                      notes: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO buttons(remote_id, name, protocol, code, bit_length,
                       pulse_length, timings, fingerprint, repeats, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (remote_id, name.strip(), protocol, code, bit_length, pulse_length,
                 json.dumps(timings), fingerprint, repeats, notes, now_iso()),
            )
            bid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO button_samples(button_id, code, fingerprint, timings, captured_at) VALUES (?,?,?,?,?)",
                (bid, code, fingerprint, json.dumps(timings), now_iso()))
        return self.get_button(bid)  # type: ignore[return-value]

    def add_sample(self, button_id: int, timings: list[int], fingerprint: str,
                   code: Optional[int]) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO button_samples(button_id, code, fingerprint, timings, captured_at) VALUES (?,?,?,?,?)",
                (button_id, code, fingerprint, json.dumps(timings), now_iso()))
        return self.get_button(button_id)  # type: ignore[return-value]

    def list_samples(self, button_id: Optional[int] = None) -> list[dict[str, Any]]:
        if button_id is None:
            return self._rows("SELECT * FROM button_samples ORDER BY button_id, id")
        return self._rows("SELECT * FROM button_samples WHERE button_id = ? ORDER BY id", (button_id,))

    def find_sample_by_fingerprint(self, fingerprint: str) -> Optional[dict[str, Any]]:
        return self._one(
            """SELECT b.*, r.name AS remote_name FROM button_samples s
               JOIN buttons b ON b.id = s.button_id JOIN remotes r ON r.id = b.remote_id
               WHERE s.fingerprint = ? LIMIT 1""", (fingerprint,))

    def buttons_with_samples(self) -> list[dict[str, Any]]:
        """Every button with its list of sample codes, for pattern matching."""
        buttons = self.list_buttons()
        samples = self.list_samples()
        by_btn: dict[int, list[dict[str, Any]]] = {}
        for smp in samples:
            by_btn.setdefault(smp["button_id"], []).append(smp)
        for b in buttons:
            b["samples"] = by_btn.get(b["id"], [])
            b["sample_count"] = len(b["samples"])
        return buttons

    def rename_button(self, button_id: int, name: str) -> Optional[dict[str, Any]]:
        with self._lock, self._conn:
            self._conn.execute("UPDATE buttons SET name = ? WHERE id = ?", (name.strip(), button_id))
        return self.get_button(button_id)

    def delete_button(self, button_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM buttons WHERE id = ?", (button_id,))
            return cur.rowcount > 0

    def find_button_by_fingerprint(self, fingerprint: str) -> Optional[dict[str, Any]]:
        b = self._one(self._BTN_SELECT + " WHERE b.fingerprint = ? LIMIT 1", (fingerprint,))
        return b or self.find_sample_by_fingerprint(fingerprint)

    # ---- captures ----------------------------------------------------------
    def log_capture(self, timings: list[int], fingerprint: str, repeats: int,
                    protocol: Optional[str], code: Optional[int],
                    bit_length: Optional[int], pulse_length: Optional[int],
                    button_id: Optional[int]) -> dict[str, Any]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """INSERT INTO captures(captured_at, protocol, code, bit_length,
                       pulse_length, timings, fingerprint, repeats, button_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (now_iso(), protocol, code, bit_length, pulse_length,
                 json.dumps(timings), fingerprint, repeats, button_id),
            )
            cid = cur.lastrowid
        return self._one("SELECT * FROM captures WHERE id = ?", (cid,))  # type: ignore[return-value]

    def get_capture(self, capture_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM captures WHERE id = ?", (capture_id,))

    def recent_captures(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._rows(
            """SELECT c.*, b.name AS button_name, r.name AS remote_name
               FROM captures c LEFT JOIN buttons b ON b.id = c.button_id
               LEFT JOIN remotes r ON r.id = b.remote_id
               ORDER BY c.id DESC LIMIT ?""",
            (limit,),
        )

    def link_capture(self, capture_id: int, button_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE captures SET button_id = ? WHERE id = ?", (button_id, capture_id))

    def clear_captures(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM captures")
            return cur.rowcount

    # ---- export ------------------------------------------------------------
    def export(self) -> dict[str, Any]:
        remotes = self.list_remotes()
        for r in remotes:
            r["buttons"] = self.list_buttons(r["id"])
            for b in r["buttons"]:
                b["samples"] = [{"code": smp["code"], "timings": smp["timings"], "captured_at": smp["captured_at"]}
                                for smp in self.list_samples(b["id"])]
        return {"exported_at": now_iso(), "remotes": remotes}
