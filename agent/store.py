"""분석 결과와 내 결정을 SQLite에 저장한다.

리포트는 JSON 그대로 넣고(구조가 바뀌어도 옛 기록을 읽을 수 있게), 목록에 보일 값만 열로 뺀다.
연결은 호출마다 열고 닫는다(여러 스레드에서 안전).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel

from agent.paths import DB_PATH
from agent.schemas import Report

Decision = Literal["지원함", "보류", "스킵"]
DECISIONS: tuple[Decision, ...] = ("지원함", "보류", "스킵")

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    score       INTEGER NOT NULL,
    verdict     TEXT NOT NULL,
    report_json TEXT NOT NULL,
    report_md   TEXT NOT NULL,
    decision    TEXT,
    decided_at  TEXT
);
"""


class AnalysisSummary(BaseModel):
    id: int
    created_at: str
    company: str
    title: str
    score: int
    verdict: str
    decision: Decision | None


class StoredAnalysis(AnalysisSummary):
    report: Report
    report_md: str


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_analysis(report: Report, report_md: str, path: Path = DB_PATH) -> int:
    with _connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO analyses (created_at, company, title, score, verdict, report_json, report_md) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), report.posting.company, report.posting.title, report.score, report.verdict,
             report.model_dump_json(), report_md),
        )
        return int(cursor.lastrowid)


def set_decision(analysis_id: int, decision: Decision, path: Path = DB_PATH) -> bool:
    """결정을 기록한다. 해당 id가 없으면 False."""
    with _connect(path) as conn:
        cursor = conn.execute(
            "UPDATE analyses SET decision = ?, decided_at = ? WHERE id = ?", (decision, _now(), analysis_id),
        )
        return cursor.rowcount > 0


def list_analyses(path: Path = DB_PATH, limit: int = 200) -> list[AnalysisSummary]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, created_at, company, title, score, verdict, decision "
            "FROM analyses ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [AnalysisSummary(**dict(row)) for row in rows]


def get_analysis(analysis_id: int, path: Path = DB_PATH) -> StoredAnalysis | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    return StoredAnalysis(report=Report.model_validate_json(data.pop("report_json")), **data)
