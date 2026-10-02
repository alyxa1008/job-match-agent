"""LLM 진입점 테스트. 실제 API를 부르지 않고 가짜 클라이언트로 재시도·기록·캐시를 확인한다."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
import pytest
from openai import InternalServerError, RateLimitError
from openai.types.chat import ChatCompletionMessage

from agent import llm, llm_cache

OK = SimpleNamespace(
    choices=[SimpleNamespace(message=ChatCompletionMessage(role="assistant", content="응답"))],
    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
)
MESSAGES = [{"role": "user", "content": "안녕"}]


def api_error(cls, status: int, message: str):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://llm.example.com"))
    return cls(message, response=response, body=None)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """가짜 클라이언트. 대기는 실제로 자지 않고 기록만 하고, 캐시는 끈 상태로 임시 폴더를 쓴다."""
    fake = MagicMock()
    fake.sleeps = []
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm.time, "sleep", fake.sleeps.append)
    monkeypatch.setattr(llm_cache, "LLM_CACHE_DIR", tmp_path)
    monkeypatch.setenv("LLM_MODEL", "default-model")
    monkeypatch.delenv("LLM_CACHE", raising=False)
    monkeypatch.delenv("LLM_MODEL_EXTRACT", raising=False)
    return fake


# --- 재시도 ---

def test_per_minute_limit_waits_for_server_hint_then_succeeds(client):
    client.chat.completions.create.side_effect = [api_error(RateLimitError, 429, "'retryDelay': '46s'"), OK]
    assert llm.chat(MESSAGES, node="extract").content == "응답"
    assert client.sleeps == [47.0]


def test_daily_limit_fails_immediately_with_reset_time(client):
    client.chat.completions.create.side_effect = [api_error(RateLimitError, 429, "'retryDelay': '36000s'")]
    with pytest.raises(llm.LLMRateLimitError, match="일일 한도.*10.0시간"):
        llm.chat(MESSAGES, node="extract")
    assert client.sleeps == []


def test_server_busy_backs_off_exponentially(client):
    busy = api_error(InternalServerError, 503, "high demand")
    client.chat.completions.create.side_effect = [busy, busy, busy, OK]
    llm.chat(MESSAGES, node="extract")
    assert client.sleeps == [2.0, 4.0, 8.0]


def test_server_busy_gives_up_after_retries(client):
    client.chat.completions.create.side_effect = [api_error(InternalServerError, 503, "high demand")] * 4
    with pytest.raises(llm.LLMUnavailableError):
        llm.chat(MESSAGES, node="extract")


# --- 노드별 모델 ---

def test_node_model_override(client, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_EXTRACT", "vision-model")
    client.chat.completions.create.return_value = OK
    llm.chat(MESSAGES, node="extract")
    llm.chat(MESSAGES, node="judge")
    models = [call.kwargs["model"] for call in client.chat.completions.create.call_args_list]
    assert models == ["vision-model", "default-model"]


# --- 호출 기록 ---

def test_calls_are_collected_only_inside_tracking_block(client):
    client.chat.completions.create.return_value = OK
    llm.chat(MESSAGES, node="extract")  # 블록 밖: 어디에도 쌓이지 않는다
    with llm.track_calls() as calls:
        llm.chat(MESSAGES, node="match")
        llm.chat(MESSAGES, node="judge")
    llm.chat(MESSAGES, node="draft")
    assert [record.node for record in calls] == ["match", "judge"]
    assert calls[0].prompt_tokens == 3 and not calls[0].cached


def test_tracking_blocks_do_not_share_records(client):
    client.chat.completions.create.return_value = OK
    with llm.track_calls() as first:
        llm.chat(MESSAGES, node="extract")
    with llm.track_calls() as second:
        llm.chat(MESSAGES, node="judge")
    assert len(first) == 1 and len(second) == 1


# --- 캐시 ---

def test_cache_replays_response_without_calling_api(client, monkeypatch):
    monkeypatch.setenv("LLM_CACHE", "1")
    client.chat.completions.create.return_value = OK
    with llm.track_calls() as calls:
        first = llm.chat(MESSAGES, node="extract")
        second = llm.chat(MESSAGES, node="extract")
        llm.chat([{"role": "user", "content": "다른 요청"}], node="extract")
    assert first.content == second.content == "응답"
    assert client.chat.completions.create.call_count == 2
    assert [record.cached for record in calls] == [False, True, False]


def test_cache_is_off_by_default(client):
    client.chat.completions.create.return_value = OK
    llm.chat(MESSAGES, node="extract")
    llm.chat(MESSAGES, node="extract")
    assert client.chat.completions.create.call_count == 2
