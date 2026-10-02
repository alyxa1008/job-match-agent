"""전체 흐름의 분기 테스트. 각 노드를 가짜로 바꿔 어떤 노드가 불리는지만 확인한다."""

from datetime import date

import pytest

from agent import graph
from agent.judge import Judgement
from agent.profile import Employment, Me, Profile
from agent.schemas import CompanyInfo, MatchResult
from tests.factories import make_posting

PROFILE = Profile(me=Me(employment=[
    Employment(company="예시테크", start=date(2024, 1, 1), end=date(2026, 3, 31), type="정규직"),
]))


@pytest.fixture
def nodes(monkeypatch):
    """노드를 가짜로 바꾸고, 불린 노드 이름을 순서대로 기록한다."""
    called: list[str] = []
    state = {"posting": make_posting(), "score": 4}

    def record(name, value):
        def node(*args, **kwargs):
            called.append(name)
            return value() if callable(value) else value
        return node

    verdicts = {4: "지원 가능", 2: "스킵 권장"}
    monkeypatch.setattr(graph, "extract", record("extract", lambda: state["posting"]))
    monkeypatch.setattr(graph, "load_profile", lambda: PROFILE)
    monkeypatch.setattr(graph, "load_resume", lambda: "이력서")
    monkeypatch.setattr(graph, "match", record("match", MatchResult(matched=[], gaps=[], match_ratio=0.0)))
    monkeypatch.setattr(graph, "research", record("research", CompanyInfo(facts=[], warnings=[], found=False)))
    monkeypatch.setattr(graph, "judge", record("judge", lambda: Judgement(
        score=state["score"], verdict=verdicts[state["score"]], reasons=["이유"], key_gaps=[])))
    monkeypatch.setattr(graph, "write_draft", record("draft", "초안"))
    return called, state


def test_passing_posting_runs_every_node(nodes):
    called, _ = nodes
    report = graph.analyze("공고")
    assert called == ["extract", "match", "research", "judge", "draft"]
    assert report.motivation_draft == "초안"


def test_hard_fail_skips_match_research_and_draft(nodes):
    called, state = nodes
    state["posting"] = make_posting(min_years=5.0)
    report = graph.analyze("공고")
    assert called == ["extract"]
    assert (report.score, report.verdict) == (1, "스킵 권장")
    assert report.match is None and report.company is None and report.motivation_draft is None


def test_low_score_skips_draft(nodes):
    called, state = nodes
    state["score"] = 2
    report = graph.analyze("공고")
    assert "draft" not in called
    assert report.motivation_draft is None
