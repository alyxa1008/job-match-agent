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
