"""회사 조사 루프 테스트. LLM과 도구를 가짜로 바꿔 흐름과 출처 검증만 확인한다."""

import json

import pytest
from openai.types.chat import ChatCompletionMessage

from agent import llm, research
from agent.tools import ToolOutput
from tests.factories import make_posting

SEARCH_URL = "https://news.example.com/ablelab-seed"
SEARCH_RESULT = json.dumps([{"title": "에이블랩 시드 투자 유치", "url": SEARCH_URL, "snippet": "2024년 설립, 시드 투자 유치"}])
SEARCH_OUTPUT = ToolOutput(SEARCH_RESULT, frozenset({SEARCH_URL}))


def tool_call_reply(*queries: str) -> ChatCompletionMessage:
    calls = [
        {"id": f"call_{i}", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": q})}}
        for i, q in enumerate(queries)
    ]
    return ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": calls})


def final_reply(facts: list[tuple[str, str]], warnings: list[str] | None = None) -> ChatCompletionMessage:
    body = {
        "facts": [{"text": text, "source_url": url} for text, url in facts],
        "warnings": warnings or [],
        "found": bool(facts),
    }
    return ChatCompletionMessage(role="assistant", content=json.dumps(body, ensure_ascii=False))


@pytest.fixture
def fake_llm(monkeypatch):
    """미리 정한 응답을 차례로 돌려주는 가짜 llm.chat. 호출 기록을 남긴다."""
    calls: list[dict] = []
    replies: list[ChatCompletionMessage] = []

    def fake_chat(messages, *, node, **kwargs):
        calls.append({"messages": list(messages), "kwargs": kwargs})
        return replies[len(calls) - 1]

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(research, "run_tool", lambda name, arguments: SEARCH_OUTPUT)
    return calls, replies


def test_search_then_answer_keeps_only_sourced_facts(fake_llm):
    calls, replies = fake_llm
    replies += [
        tool_call_reply("에이블랩 투자"),
        final_reply([("2024년 설립, 시드 투자 유치", SEARCH_URL), ("직원 약 500명", "https://made-up.example.com/x")]),
    ]
    info = research.research(make_posting())
    assert len(calls) == 2
    assert [fact.text for fact in info.facts] == ["2024년 설립, 시드 투자 유치"]
    assert info.found


def test_tool_results_are_sent_back_to_the_model(fake_llm):
    calls, replies = fake_llm
    replies += [tool_call_reply("에이블랩 투자"), final_reply([])]
    research.research(make_posting())
    second_call_messages = calls[1]["messages"]
    assert second_call_messages[-1] == {"role": "tool", "tool_call_id": "call_0", "content": SEARCH_RESULT}
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == "web_search"


def test_loop_stops_at_call_limit_and_forces_final_answer(fake_llm):
    calls, replies = fake_llm
    replies += [tool_call_reply("검색 1"), tool_call_reply("검색 2"), tool_call_reply("검색 3"),
                final_reply([("2024년 설립, 시드 투자 유치", SEARCH_URL)])]
    info = research.research(make_posting())
    assert len(calls) == research.MAX_LLM_CALLS
    assert "tools" not in calls[-1]["kwargs"]  # 마지막 호출은 도구 없이 JSON만
    assert info.found


def test_extra_tool_calls_in_one_round_are_skipped(fake_llm):
    calls, replies = fake_llm
    replies += [tool_call_reply("a", "b", "c", "d"), final_reply([])]
    research.research(make_posting())
    tool_messages = [m for m in calls[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 4
    assert tool_messages[3]["content"].startswith("[건너뜀]")


def test_nothing_found_reports_not_found_without_warnings(fake_llm):
    calls, replies = fake_llm
    replies += [tool_call_reply("에이블랩"), final_reply([("매출 급감", "https://made-up.example.com")], ["매출 급감"])]
    info = research.research(make_posting())
    assert not info.found
    assert info.facts == [] and info.warnings == []
