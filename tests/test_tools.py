"""도구 테스트. 네트워크를 쓰지 않는다(차단 규칙과 run_tool의 실패 처리만 확인)."""

import json

import pytest

from agent import tools
from agent.tools import FetchError, ToolOutput, fetch_page, run_tool


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", "이 사이트는 가져오지 않습니다"),
        ("https://jobplanet.co.kr/companies/1", "이 사이트는 가져오지 않습니다"),
        ("http://localhost:8000/admin", "내부 주소"),
        ("http://127.0.0.1/", "내부 주소"),
        ("http://192.168.0.10/", "내부 주소"),
        ("ftp://example.com/file", "주소가 아닙니다"),
    ],
)
def test_fetch_page_refuses_blocked_urls(url, reason):
    with pytest.raises(FetchError, match=reason):
        fetch_page(url)


def test_run_tool_web_search_reports_result_urls(monkeypatch):
    results = [{"title": "에이블랩", "url": "https://news.example.com/a", "snippet": "시드 투자"},
               {"title": "주소 없음", "url": "", "snippet": ""}]
    monkeypatch.setattr(tools, "web_search", lambda query: results)
    output = run_tool("web_search", json.dumps({"query": "에이블랩 투자"}))
    assert json.loads(output.content) == results
    assert output.source_urls == {"https://news.example.com/a"}


def test_run_tool_fetch_page_reports_url_on_success(monkeypatch):
    monkeypatch.setattr(tools, "fetch_page", lambda url: "본문")
    output = run_tool("fetch_page", '{"url": "https://example.com/a"}')
    assert output == ToolOutput("본문", frozenset({"https://example.com/a"}))


def test_run_tool_blocked_fetch_has_no_source_url():
    output = run_tool("fetch_page", '{"url": "https://www.saramin.co.kr/x"}')
    assert output.content.startswith("[가져오지 않음]")
    assert output.source_urls == frozenset()


@pytest.fixture
def cache_on(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE", "1")
    monkeypatch.setattr(tools.llm_cache, "LLM_CACHE_DIR", tmp_path)


def test_cached_tool_result_is_replayed_even_if_search_changes(monkeypatch, cache_on):
    """검색 결과는 실행마다 달라질 수 있다. 캐시가 켜져 있으면 처음 결과를 그대로 재생한다."""
    answers = iter([
        [{"title": "첫 결과", "url": "https://news.example.com/1", "snippet": ""}],
        [{"title": "달라진 결과", "url": "https://news.example.com/2", "snippet": ""}],
    ])
    monkeypatch.setattr(tools, "web_search", lambda query: next(answers))
    first = run_tool("web_search", '{"query": "에이블랩"}')
    second = run_tool("web_search", '{"query": "에이블랩"}')
    assert first == second
    assert second.source_urls == {"https://news.example.com/1"}


def test_failed_tool_result_is_not_cached(monkeypatch, cache_on):
    calls = []

    def flaky_fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise FetchError("[가져오기 실패] HTTP 503")
        return "본문"

    monkeypatch.setattr(tools, "fetch_page", flaky_fetch)
    assert run_tool("fetch_page", '{"url": "https://example.com/a"}').content.startswith("[가져오기 실패]")
    assert run_tool("fetch_page", '{"url": "https://example.com/a"}').content == "본문"


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("unknown_tool", "{}", "[오류] 없는 도구"),
        ("web_search", "not json", "[오류] JSONDecodeError"),
        ("web_search", "{}", "[오류] KeyError"),
    ],
)
def test_run_tool_never_raises(name, arguments, expected):
    output = run_tool(name, arguments)
    assert output.content.startswith(expected)
    assert output.source_urls == frozenset()
