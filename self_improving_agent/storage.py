"""SQLite persistence plus an append-only JSONL event log."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .logutil import redact


SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  mint TEXT NOT NULL,
  venue TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  score REAL,
  decision TEXT,
  snapshot_json TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  checkpoints_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (mint, venue)
);
CREATE TABLE IF NOT EXISTS orders (
  client_order_id TEXT PRIMARY KEY,
  mint TEXT NOT NULL,
  side TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  qty_tokens REAL,
  price_usd REAL,
  fee_usd REAL,
  tx TEXT,
  ts REAL NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_order_id TEXT NOT NULL,
  mint TEXT NOT NULL,
  side TEXT NOT NULL,
  qty_tokens REAL NOT NULL,
  price_usd REAL NOT NULL,
  fee_usd REAL NOT NULL,
  tx TEXT,
  ts REAL NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
  mint TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  venue TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS missed_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  venue TEXT NOT NULL,
  label TEXT NOT NULL,
  ts REAL NOT NULL,
  detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  ts REAL NOT NULL,
  summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weight_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL,
  ts REAL NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "agent.db"
        self.events_path = self.data_dir / "events.jsonl"
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def event(self, kind: str, **payload: Any) -> None:
        row = {"ts": time.time(), "kind": kind, **redact(payload)}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")

    def upsert_candidate(
        self,
        mint: str,
        venue: str,
        snapshot: dict[str, Any],
        raw: dict[str, Any],
        score: float,
        decision: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = now if now is not None else time.time()
        existing = self._conn.execute(
            "SELECT first_seen, checkpoints_json, raw_json FROM candidates WHERE mint=? AND venue=?",
            (mint, venue),
        ).fetchone()
        if existing:
            first_seen = existing["first_seen"]
            checkpoints = json.loads(existing["checkpoints_json"])
            prior_raw = json.loads(existing["raw_json"])
            if isinstance(prior_raw, dict):
                raw = {**prior_raw, **raw}
        else:
            first_seen = now
            checkpoints = {}
        self._conn.execute(
            """
            INSERT INTO candidates (mint, venue, first_seen, last_seen, score, decision, snapshot_json, raw_json, checkpoints_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mint, venue) DO UPDATE SET
              last_seen=excluded.last_seen,
              score=excluded.score,
              decision=excluded.decision,
              snapshot_json=excluded.snapshot_json,
              raw_json=excluded.raw_json,
              checkpoints_json=excluded.checkpoints_json
            """,
            (
                mint,
                venue,
                first_seen,
                now,
                score,
                decision,
                json.dumps(snapshot),
                json.dumps(raw),
                json.dumps(checkpoints),
            ),
        )
        self._conn.commit()
        self.event("candidate", mint=mint, venue=venue, score=score, decision=decision)
        return {"first_seen": first_seen, "checkpoints": checkpoints}

    def update_checkpoints(self, mint: str, venue: str, checkpoints: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE candidates SET checkpoints_json=? WHERE mint=? AND venue=?",
            (json.dumps(checkpoints), mint, venue),
        )
        self._conn.commit()

    def list_candidates(self, since: float | None = None) -> list[dict[str, Any]]:
        if since is None:
            rows = self._conn.execute("SELECT * FROM candidates").fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM candidates WHERE last_seen>=?", (since,)).fetchall()
        return [self._candidate_row(row) for row in rows]

    def watched_candidates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE decision IN ('skip', 'watch')"
        ).fetchall()
        return [self._candidate_row(row) for row in rows]

    def _candidate_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "mint": row["mint"],
            "venue": row["venue"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "score": row["score"],
            "decision": row["decision"],
            "snapshot": json.loads(row["snapshot_json"]),
            "raw": json.loads(row["raw_json"]),
            "checkpoints": json.loads(row["checkpoints_json"]),
        }

    def save_order(self, order: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders
            (client_order_id, mint, side, mode, status, qty_tokens, price_usd, fee_usd, tx, ts, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["client_order_id"],
                order["mint"],
                order["side"],
                order["mode"],
                order["status"],
                order.get("qty_tokens", 0),
                order.get("price_usd", 0),
                order.get("fee_usd", 0),
                order.get("tx", ""),
                order["ts"],
                json.dumps(redact(order.get("raw", {}))),
            ),
        )
        self._conn.commit()
        self.event("order", **{k: order[k] for k in ("client_order_id", "mint", "side", "mode", "status")})

    def order_exists(self, client_order_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM orders WHERE client_order_id=?", (client_order_id,)
        ).fetchone()
        return row is not None

    def save_fill(self, fill: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO fills (client_order_id, mint, side, qty_tokens, price_usd, fee_usd, tx, ts, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill["client_order_id"],
                fill["mint"],
                fill["side"],
                fill["qty_tokens"],
                fill["price_usd"],
                fill["fee_usd"],
                fill.get("tx", ""),
                fill["ts"],
                json.dumps(redact(fill.get("raw", {}))),
            ),
        )
        self._conn.commit()
        self.event("fill", mint=fill["mint"], side=fill["side"], qty=fill["qty_tokens"], price=fill["price_usd"])

    def recent_fills(self, since: float) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM fills WHERE ts>=? ORDER BY ts", (since,)).fetchall()
        return [dict(row) for row in rows]

    def save_position(self, position: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO positions (mint, state, venue, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mint) DO UPDATE SET
              state=excluded.state,
              venue=excluded.venue,
              payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
            """,
            (
                position["mint"],
                position["state"],
                position["venue"],
                json.dumps(position),
                time.time(),
            ),
        )
        self._conn.commit()

    def load_positions(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT payload_json FROM positions").fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def open_positions(self) -> list[dict[str, Any]]:
        return [p for p in self.load_positions() if p.get("state") != "FLAT"]

    def add_missed(self, event: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO missed_events (mint, venue, label, ts, detail_json) VALUES (?, ?, ?, ?, ?)",
            (event["mint"], event["venue"], event["label"], event["ts"], json.dumps(event)),
        )
        self._conn.commit()
        self.event("outcome", mint=event["mint"], venue=event["venue"], label=event["label"])

    def missed_since(self, since: float) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT detail_json FROM missed_events WHERE ts>=? ORDER BY ts", (since,)
        ).fetchall()
        return [json.loads(row["detail_json"]) for row in rows]

    def index_report(self, kind: str, path: str, summary: str, ts: float | None = None) -> None:
        self._conn.execute(
            "INSERT INTO reports_index (kind, path, ts, summary) VALUES (?, ?, ?, ?)",
            (kind, path, ts if ts is not None else time.time(), summary),
        )
        self._conn.commit()

    def save_weights(self, version: int, payload: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO weight_versions (version, ts, payload_json) VALUES (?, ?, ?)",
            (version, time.time(), json.dumps(payload)),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()
