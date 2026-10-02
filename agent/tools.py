"""research 노드가 쓰는 도구: web_search, fetch_page.

모델이 부르는 입구는 run_tool() 하나다. 실패해도 예외를 던지지 않고 사유를 내용으로 돌려준다
(결과가 그대로 모델에게 가서, 모델이 다른 방법을 고르게 하기 위해).
채용·리뷰 사이트(사람인, 잡플래닛 등)는 가져오지 않는다. 검색 결과 스니펫까지만 쓴다.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx2
import trafilatura
from ddgs import DDGS

for noisy_logger in ("ddgs", "primp"):  # 검색 엔진별 요청 로그는 끈다
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

USER_AGENT = "job-match-agent/0.1 (personal job research tool)"
SEARCH_REGION = "kr-kr"
MAX_SEARCH_RESULTS = 5
MAX_PAGE_CHARS = 8000
MAX_DOWNLOAD_BYTES = 2_000_000  # 이보다 큰 응답은 앞부분만 읽는다
TIMEOUT_SEC = 10.0
TEXT_CONTENT_TYPES = ("text/", "application/xhtml+xml")
NO_FETCH_DOMAINS = ("saramin.co.kr", "jobplanet.co.kr", "jobkorea.co.kr", "linkedin.com", "teamblind.com")

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "웹 검색. 결과마다 title, url, snippet을 돌려준다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "검색어"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "URL의 본문 텍스트를 가져온다(최대 8,000자). 검색 결과에 나온 URL에만 쓴다.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "가져올 페이지 주소"}},
                "required": ["url"],
            },
        },
    },
]


@dataclass(frozen=True)
class ToolOutput:
    content: str  # 모델에게 돌려줄 내용
    source_urls: frozenset[str] = frozenset()  # 이 결과를 출처로 인용할 수 있는 URL


class FetchError(Exception):
    """페이지를 가져오지 않았거나 가져오지 못함. 메시지는 모델에게 그대로 전달된다."""


def web_search(query: str) -> list[dict[str, str]]:
    results = DDGS().text(query, region=SEARCH_REGION, max_results=MAX_SEARCH_RESULTS)
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in results]


def _download_text(url: str) -> str:
    """문서를 MAX_DOWNLOAD_BYTES까지만 읽는다. 큰 파일이나 바이너리를 통째로 메모리에 올리지 않도록 스트리밍한다."""
    with httpx2.stream("GET", url, headers={"User-Agent": USER_AGENT},
                       timeout=TIMEOUT_SEC, follow_redirects=True) as response:
        if response.status_code != 200:
            raise FetchError(f"[가져오기 실패] HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.startswith(TEXT_CONTENT_TYPES):
            raise FetchError("[가져오지 않음] 웹 문서가 아닙니다")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_DOWNLOAD_BYTES:
                break
        return b"".join(chunks)[:MAX_DOWNLOAD_BYTES].decode(response.encoding or "utf-8", errors="replace")


def _is_private_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False  # IP가 아니라 도메인 이름


@lru_cache(maxsize=64)
def _robots_for(origin: str) -> RobotFileParser:
    """사이트의 robots.txt. 없거나 읽지 못하면 허용으로 본다."""
    parser = RobotFileParser()
    try:
        parser.parse(_download_text(f"{origin}/robots.txt").splitlines())
    except (FetchError, httpx2.HTTPError):
        parser.parse([])
    return parser


def _blocked_reason(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return "http(s) 주소가 아닙니다"
    if _is_private_host(host):
        return "내부 주소는 가져오지 않습니다"
    if any(host == domain or host.endswith(f".{domain}") for domain in NO_FETCH_DOMAINS):
        return "이 사이트는 가져오지 않습니다(검색 결과 스니펫만 사용)"
    if not _robots_for(f"{parts.scheme}://{parts.netloc}").can_fetch(USER_AGENT, url):
        return "robots.txt가 수집을 막고 있습니다"
    return None


def fetch_page(url: str) -> str:
    """페이지 본문 텍스트(최대 MAX_PAGE_CHARS자). 가져오지 않거나 못 가져오면 FetchError."""
    reason = _blocked_reason(url)
    if reason:
        raise FetchError(f"[가져오지 않음] {reason}")
    try:
        html = _download_text(url)
    except httpx2.HTTPError as exc:
        raise FetchError(f"[가져오기 실패] {type(exc).__name__}") from exc
    text = trafilatura.extract(html)
    if not text:
        raise FetchError("[본문 없음] 텍스트를 추출하지 못했습니다(로그인·스크립트 페이지일 수 있음)")
    return text[:MAX_PAGE_CHARS]


def run_tool(name: str, arguments: str) -> ToolOutput:
    """모델이 요청한 도구를 실행한다. 어떤 실패든 예외 대신 사유를 담은 ToolOutput으로 돌려준다."""
    try:
        args = json.loads(arguments or "{}")
        if name == "web_search":
            results = web_search(str(args["query"]))
            urls = frozenset(result["url"] for result in results if result["url"])
            return ToolOutput(json.dumps(results, ensure_ascii=False), urls)
        if name == "fetch_page":
            url = str(args["url"])
            return ToolOutput(fetch_page(url), frozenset({url}))
        return ToolOutput(f"[오류] 없는 도구: {name}")
    except FetchError as exc:
        return ToolOutput(str(exc))
    except Exception as exc:  # 도구 실패는 모델이 보고 다른 방법을 고르게 한다
        return ToolOutput(f"[오류] {type(exc).__name__}: {exc}")
