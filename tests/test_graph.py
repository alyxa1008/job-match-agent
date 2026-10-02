"""전체 흐름의 분기 테스트. 각 노드를 가짜로 바꿔 어떤 노드가 어떻게 불리는지만 확인한다."""

import threading
from datetime import date
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage

from agent import graph, llm
from agent.judge import Judgement
from agent.profile import Employment, Me, Profile
from agent.schemas import CompanyInfo, MatchResult
from tests.factories import make_posting

PROFILE = Profile(me=Me(employment=[
    Employment(company="예시테크", start=date(2024, 1, 1), end=date(2026, 3, 31), type="정규직"),
]))
NO_MATCH = MatchResult(matched=[], gaps=[], match_ratio=0.0)
NO_COMPANY = CompanyInfo(facts=[], warnings=[], found=False)
VERDICTS = {4: "지원 가능", 2: "스킵 권장"}


@pytest.fixture
def nodes(monkeypatch):
    """노드를 가짜로 바꾸고, 불린 노드 이름을 기록한다. state로 공고와 추천도를 바꿀 수 있다."""
    called: list[str] = []
    state = {"posting": make_posting(), "score": 4}

    def record(name, value):
        def node(*args, **kwargs):
            called.append(name)
            return value() if callable(value) else value
        return node

    monkeypatch.setattr(graph, "extract", record("extract", lambda: state["posting"]))
    monkeypatch.setattr(graph, "load_profile", lambda: PROFILE)
    monkeypatch.setattr(graph, "load_resume", lambda: "이력서")
    monkeypatch.setattr(graph, "match", record("match", NO_MATCH))
    monkeypatch.setattr(graph, "research", record("research", NO_COMPANY))
    monkeypatch.setattr(graph, "judge", record("judge", lambda: Judgement(
        score=state["score"], verdict=VERDICTS[state["score"]], reasons=["이유"], key_gaps=[])))
    monkeypatch.setattr(graph, "write_draft", record("draft", "초안"))
    return called, state


def test_passing_posting_runs_every_node(nodes):
    called, _ = nodes
    report = graph.analyze("공고")
    assert called[0] == "extract"
    assert set(called[1:3]) == {"match", "research"}  # 병렬이라 둘 사이 순서는 정해져 있지 않다
    assert called[3:] == ["judge", "draft"]
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


def test_match_and_research_run_at_the_same_time(nodes, monkeypatch):
    both_running = threading.Barrier(2, timeout=5)  # 두 노드가 동시에 도착해야 통과한다

    def wait_for_other(value):
        def node(*args, **kwargs):
            both_running.wait()
            return value
        return node

    monkeypatch.setattr(graph, "match", wait_for_other(NO_MATCH))
    monkeypatch.setattr(graph, "research", wait_for_other(NO_COMPANY))
    assert graph.analyze("공고").verdict == "지원 가능"


def test_research_failure_does_not_stop_the_report(nodes, monkeypatch):
    called, _ = nodes

    def failing_research(posting):
        raise llm.LLMUnavailableError("서버 혼잡")

    monkeypatch.setattr(graph, "research", failing_research)
    report = graph.analyze("공고")
    assert report.company.error == "서버 혼잡"
    assert not report.company.found
    assert called[-2:] == ["judge", "draft"]


def test_calls_from_parallel_nodes_are_tracked(nodes, monkeypatch):
    """병렬 노드는 다른 스레드에서 돌지만, 그 안의 LLM 호출도 같은 track_calls 블록에 모여야 한다."""
    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=ChatCompletionMessage(role="assistant", content="응답"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: reply)))
    monkeypatch.setattr(llm, "_client", fake_client)
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.delenv("LLM_CACHE", raising=False)

    def calls_llm(node_name, value):
        def node(*args, **kwargs):
            llm.chat([{"role": "user", "content": "안녕"}], node=node_name)
            return value
        return node

    monkeypatch.setattr(graph, "match", calls_llm("match", NO_MATCH))
    monkeypatch.setattr(graph, "research", calls_llm("research", NO_COMPANY))
    with llm.track_calls() as calls:
        graph.analyze("공고")
    assert sorted(record.node for record in calls) == ["match", "research"]
    assert graph.call_summary(calls).startswith("LLM 호출 2회")
