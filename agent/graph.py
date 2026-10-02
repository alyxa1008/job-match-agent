"""전체 흐름: extract → filters → (match, research) → judge → draft → report.

M3: 순차 함수 호출. (M4에서 LangGraph로 옮기며 match·research를 병렬로 돌린다.)
"""

from __future__ import annotations

import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Sequence

from agent import llm
from agent.extract import PostingImage, extract, load_posting
from agent.filters import run_filters
from agent.judge import DRAFT_MIN_SCORE, judge, judge_failed, write_draft
from agent.match import load_resume, match
from agent.profile import load_profile
from agent.report import render_report
from agent.research import research
from agent.schemas import Report

logger = logging.getLogger(__name__)


def analyze(text: str = "", images: Sequence[PostingImage] = ()) -> Report:
    posting = extract(text, images)
    profile = load_profile()
    filters = run_filters(posting, profile)

    if filters.overall == "FAIL":  # 조건 미달이면 매칭·회사 조사를 생략한다
        failed = judge_failed(filters)
        return Report(posting=posting, filters=filters, match=None, company=None,
                      motivation_draft=None, **failed.model_dump())

    match_result = match(posting, load_resume())
    company = research(posting)
    judgement = judge(posting, filters, match_result, company, profile)
    draft = write_draft(posting, match_result, company) if judgement.score >= DRAFT_MIN_SCORE else None
    return Report(posting=posting, filters=filters, match=match_result, company=company,
                  motivation_draft=draft, **judgement.model_dump())


def call_summary(calls: Sequence[llm.CallRecord]) -> str:
    """노드별 LLM 호출 수. 캐시로 대신한 호출은 따로 센다."""
    real = Counter(record.node for record in calls if not record.cached)
    cached = sum(1 for record in calls if record.cached)
    per_node = ", ".join(f"{node} {count}" for node, count in real.items()) or "없음"
    return f"LLM 호출 {sum(real.values())}회 ({per_node}) / 캐시 {cached}회"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        sys.exit("사용법: python -m agent.graph <공고 파일 또는 폴더> [...]")
    started = time.perf_counter()
    with llm.track_calls() as calls:
        result = analyze(*load_posting([Path(arg) for arg in sys.argv[1:]]))
    print("\n" + render_report(result))
    logger.info("\n[요약] %.1f초, %s", time.perf_counter() - started, call_summary(calls))
