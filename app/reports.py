"""Historique des analyses (SQLite) : chaque analyse est enregistrée et partageable."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .config import Settings, get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    report_id    TEXT PRIMARY KEY,
    created_at   TEXT,
    kind         TEXT,
    symbol       TEXT,
    timeframe    TEXT,
    price        REAL,
    label        TEXT,
    score        REAL,
    confidence   REAL,
    mode         TEXT,
    provider     TEXT,
    model        TEXT,
    question     TEXT,
    answer       TEXT,
    analysis     TEXT,
    observation  TEXT,
    sources      TEXT,
    warnings     TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_symbol  ON reports(symbol);
"""


class ReportStore:
    """Petit dépôt d'analyses persistées."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------ écriture
    def save(
        self,
        *,
        kind: str,
        symbol: str = "",
        timeframe: str = "",
        price: float = 0.0,
        label: str = "",
        score: float = 0.0,
        confidence: float = 0.0,
        mode: str = "demo",
        provider: str = "",
        model: str = "",
        question: str = "",
        answer: str = "",
        analysis: Optional[dict[str, Any]] = None,
        observation: Optional[dict[str, Any]] = None,
        sources: Optional[Sequence[dict[str, Any]]] = None,
        warnings: Optional[Sequence[str]] = None,
    ) -> str:
        report_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO reports (report_id, created_at, kind, symbol, timeframe, price, "
                "label, score, confidence, mode, provider, model, question, answer, analysis, "
                "observation, sources, warnings) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    kind,
                    symbol,
                    timeframe,
                    float(price or 0.0),
                    label,
                    float(score or 0.0),
                    float(confidence or 0.0),
                    mode,
                    provider,
                    model,
                    question,
                    answer,
                    json.dumps(analysis, ensure_ascii=False) if analysis else "",
                    json.dumps(observation, ensure_ascii=False) if observation else "",
                    json.dumps(list(sources or []), ensure_ascii=False),
                    json.dumps(list(warnings or []), ensure_ascii=False),
                ),
            )
            connection.commit()
        return report_id

    def delete(self, report_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reports WHERE report_id = ?", (report_id,)
            )
            connection.commit()
        return cursor.rowcount > 0

    def clear(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM reports")
            connection.commit()
        return cursor.rowcount

    # ------------------------------------------------------------ lecture
    def list(self, limit: int = 20, offset: int = 0, symbol: str = "") -> list[dict[str, Any]]:
        query = (
            "SELECT report_id, created_at, kind, symbol, timeframe, price, label, score, "
            "confidence, mode, provider, model, question, warnings FROM reports"
        )
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol LIKE ?"
            params.append(f"%{symbol.upper()}%")
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        reports = []
        for row in rows:
            try:
                warnings = json.loads(row["warnings"] or "[]")
            except json.JSONDecodeError:
                warnings = []
            reports.append(
                {
                    "report_id": row["report_id"],
                    "created_at": row["created_at"],
                    "kind": row["kind"],
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "price": row["price"],
                    "label": row["label"],
                    "score": row["score"],
                    "confidence": row["confidence"],
                    "mode": row["mode"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "question": row["question"],
                    "warnings": warnings,
                }
            )
        return reports

    def get(self, report_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("analysis", "observation", "sources", "warnings"):
            raw = record.get(key)
            if not raw:
                record[key] = {} if key in {"analysis", "observation"} else []
                continue
            try:
                record[key] = json.loads(raw)
            except json.JSONDecodeError:
                record[key] = {} if key in {"analysis", "observation"} else []
        return record

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
            by_label = connection.execute(
                "SELECT label, COUNT(*) AS n FROM reports GROUP BY label ORDER BY n DESC LIMIT 8"
            ).fetchall()
        return {
            "total": int(total),
            "par_label": [{"label": row["label"], "nombre": row["n"]} for row in by_label],
            "path": str(self.path),
        }


_STORE: dict[str, ReportStore] = {}


def get_report_store(settings: Settings | None = None) -> ReportStore:
    settings = settings or get_settings()
    key = str(settings.reports_db_path)
    if key not in _STORE:
        _STORE[key] = ReportStore(settings.reports_db_path)
    return _STORE[key]


def clear_report_stores() -> None:
    _STORE.clear()
