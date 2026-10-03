"""전체 흐름 (LangGraph).

START → extract → filters ─ FAIL ───────────────→ skip ─────────────────→ END
                          └ PASS/WARN → match    ┐
                                        research ┘(병렬) → judge ─ 추천도 ≥ 3 → draft → END
                                                                 └ 그 외 ──────────────→ END

분기 세 가지: 하드 조건 FAIL이면 매칭·조사를 건너뛰고, 매칭과 회사 조사는 동시에 돌리며,
추천도가 낮으면 초안을 쓰지 않는다. 회사 조사가 실패해도 나머지 리포트는 끝까지 만든다.
"""

from __future__ import annotations

import logging
import operator
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, Sequence, TypedDict

from langgraph.graph import END, START, StateGraph

from agent import llm
from agent.extract import PostingImage, extract, load_posting
from agent.filters import run_filters
from agent.judge import DRAFT_MIN_SCORE, Judgement, judge, judge_failed, write_draft
from agent.llm_json import LLMOutputError
from agent.match import load_resume, match
from agent.profile import Profile, load_profile
from agent.report import render_report
from agent.research import research
from agent.schemas import CompanyInfo, HardFilterResult, JobPosting, MatchResult, Report

logger = logging.getLogger(__name__)

RESEARCH_ERRORS = (llm.LLMRateLimitError, llm.LLMUnavailableError, LLMOutputError)


class AnalysisState(TypedDict, total=False):
    text: str
    images: Sequence[PostingImage]
    posting: JobPosting
    profile: Profile
    filters: HardFilterResult
    match: MatchResult
    company: CompanyInfo
    judgement: Judgement
    draft: str
    timings: Annotated[dict[str, float], operator.or_]  # 노드 이름 → 소요 초. 병렬 노드의 기록을 합친다


def _extract(state: AnalysisState) -> dict[str, Any]:
    return {"posting": extract(state["text"], state["images"])}


def _filters(state: AnalysisState) -> dict[str, Any]:
    profile = load_profile()
    return {"profile": profile, "filters": run_filters(state["posting"], profile)}


def _skip(state: AnalysisState) -> dict[str, Any]:
    return {"judgement": judge_failed(state["filters"])}


def _match(state: AnalysisState) -> dict[str, Any]:
    return {"match": match(state["posting"], load_resume())}


def _research(state: AnalysisState) -> dict[str, Any]:
    try:
        return {"company": research(state["posting"], state["profile"].company)}
    except RESEARCH_ERRORS as exc:  # 회사 조사는 보조 정보라, 실패해도 분석을 멈추지 않는다
        logger.warning("[graph] 회사 조사 실패: %s", exc)
        return {"company": CompanyInfo(facts=[], warnings=[], found=False, error=str(exc))}


def _judge(state: AnalysisState) -> dict[str, Any]:
    judgement = judge(state["posting"], state["filters"], state["match"], state["company"], state["profile"])
    return {"judgement": judgement}


def _draft(state: AnalysisState) -> dict[str, Any]:
    return {"draft": write_draft(state["posting"], state["match"], state["company"])}


def _after_filters(state: AnalysisState) -> list[str]:
    return ["skip"] if state["filters"].overall == "FAIL" else ["match", "research"]


def _after_judge(state: AnalysisState) -> str:
    return "draft" if state["judgement"].score >= DRAFT_MIN_SCORE else END


def _timed(name: str, node: Callable[[AnalysisState], dict[str, Any]]) -> Callable[[AnalysisState], dict[str, Any]]:
    """노드 실행 시간을 state의 timings에 남긴다."""
    def run(state: AnalysisState) -> dict[str, Any]:
        started = time.perf_counter()
        update = node(state)
        return {**update, "timings": {name: time.perf_counter() - started}}
    return run


def _build_graph():
    builder = StateGraph(AnalysisState)
    nodes = {"extract": _extract, "filters": _filters, "skip": _skip, "match": _match,
             "research": _research, "judge": _judge, "draft": _draft}
    for name, node in nodes.items():
        builder.add_node(name, _timed(name, node))
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "filters")
    builder.add_conditional_edges("filters", _after_filters, ["skip", "match", "research"])
    builder.add_edge(["match", "research"], "judge")  # 둘 다 끝나야 judge로 간다
    builder.add_conditional_edges("judge", _after_judge, ["draft", END])
    builder.add_edge("skip", END)
    builder.add_edge("draft", END)
    return builder.compile()


_GRAPH = _build_graph()


def _log_timings(timings: dict[str, float], elapsed: float) -> None:
    per_node = ", ".join(f"{name} {seconds:.1f}s" for name, seconds in timings.items())
    node_total = sum(timings.values())
    logger.info("[graph] %s", per_node)
    logger.info("[graph] 노드 합계 %.1f초 → 실제 %.1f초 (병렬 실행으로 %.1f초 단축)",
                node_total, elapsed, max(node_total - elapsed, 0.0))


@dataclass(frozen=True)
class NodeDone:
    """노드 하나가 끝났다는 알림. next는 이어서 시작되는 노드들(화면의 진행 표시용)."""
    name: str
    seconds: float
    next: list[str]


def _next_nodes(name: str, state: dict[str, Any]) -> list[str]:
    if name == "extract":
        return ["filters"]
    if name == "filters":
        return _after_filters(state)
    if name in ("match", "research"):
        return ["judge"] if "match" in state and "company" in state else []
    if name == "judge":
        return ["draft"] if _after_judge(state) == "draft" else []
    return []


def _to_report(state: dict[str, Any]) -> Report:
    return Report(
        posting=state["posting"], filters=state["filters"],
        match=state.get("match"), company=state.get("company"),
        motivation_draft=state.get("draft"), **state["judgement"].model_dump(),
    )


def run(text: str = "", images: Sequence[PostingImage] = ()) -> Iterator[NodeDone | Report]:
    """노드가 끝날 때마다 NodeDone을, 마지막에 Report를 낸다. 화면이 진행 상황을 보여줄 때 쓴다."""
    started = time.perf_counter()
    state: dict[str, Any] = {"timings": {}}
    initial = {"text": text, "images": list(images), "timings": {}}
    for update in _GRAPH.stream(initial, stream_mode="updates"):
        for name, patch in update.items():
            state["timings"] |= patch.pop("timings", {})
            state.update(patch)
            yield NodeDone(name, state["timings"][name], _next_nodes(name, state))
    _log_timings(state["timings"], time.perf_counter() - started)
    yield _to_report(state)


def analyze(text: str = "", images: Sequence[PostingImage] = ()) -> Report:
    *_, report = run(text, images)
    assert isinstance(report, Report)
    return report


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
    started_at = time.perf_counter()
    with llm.track_calls() as calls:
        try:
            result = analyze(*load_posting([Path(arg) for arg in sys.argv[1:]]))
        except llm.LLMError as exc:
            sys.exit(f"분석 실패: {exc}")
    print("\n" + render_report(result))
    logger.info("\n[요약] %.1f초, %s", time.perf_counter() - started_at, call_summary(calls))
