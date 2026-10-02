"""LLM 호출 단일 진입점.

모델명·base_url·API 키는 .env에서 읽는다. 다른 모듈은 openai SDK를 직접 쓰지 않고
여기의 chat()만 호출한다. 호출마다 노드 이름·모델·소요 시간·토큰 수를 call_log에 남긴다.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import InternalServerError, OpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessage

from agent import llm_cache

load_dotenv()
logger = logging.getLogger(__name__)
logging.getLogger("httpx2").setLevel(logging.WARNING)  # 요청마다 찍히는 HTTP 로그는 끈다

MAX_RATE_LIMIT_RETRIES = 1  # 한도 초과는 금방 풀리지 않으므로 1회만
MAX_SERVER_ERROR_RETRIES = 3  # 503(혼잡)은 일시적이라 더 시도
BACKOFF_BASE_SEC = 2.0
MAX_RETRY_WAIT_SEC = 60.0
KEY_PLACEHOLDER_PREFIX = "여기에"


class LLMConfigError(RuntimeError):
    """.env 설정이 없거나 잘못됨."""


class LLMRateLimitError(RuntimeError):
    """백오프 후에도 429(한도 초과)."""


class LLMUnavailableError(RuntimeError):
    """백오프 후에도 5xx(서버 혼잡)."""


@dataclass
class CallRecord:
    node: str
    model: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int
    cached: bool  # True면 디스크 캐시 응답 (실제 호출 아님)


call_log: list[CallRecord] = []

_client: OpenAI | None = None


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith(KEY_PLACEHOLDER_PREFIX):
        raise LLMConfigError(f".env에 {name} 값이 없습니다. .env.example을 복사해 채워주세요.")
    return value


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # SDK 자체 재시도는 끄고, 429·5xx 재시도는 여기서 직접 제어한다.
        _client = OpenAI(base_url=_env("LLM_BASE_URL"), api_key=_env("LLM_API_KEY"), max_retries=0)
    return _client


def _model_for(node: str) -> str:
    """노드별 모델(LLM_MODEL_<NODE>)이 있으면 그것, 없으면 기본 LLM_MODEL. 무료 한도가 모델별이라 나눠 쓴다."""
    return os.getenv(f"LLM_MODEL_{node.upper()}", "").strip() or _env("LLM_MODEL")


def _record(node: str, model: str, seconds: float, usage: Any, *, cached: bool = False) -> None:
    record = CallRecord(
        node=node,
        model=model,
        seconds=seconds,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cached=cached,
    )
    call_log.append(record)
    if cached:
        logger.info("[llm] node=%s model=%s 캐시 사용", node, model)
        return
    logger.info(
        "[llm] node=%s model=%s %.2fs in=%d out=%d",
        node, model, record.seconds, record.prompt_tokens, record.completion_tokens,
    )


def _hinted_wait_sec(exc: Exception) -> float | None:
    """서버가 알려준 재시도 대기 시간(429 응답의 retryDelay). 분당 한도면 수십 초, 일일 한도면 수 시간."""
    match = re.search(r"retryDelay\W+([\d.]+)s", str(exc))
    return float(match.group(1)) if match else None


def _give_up_error(model: str, is_rate_limit: bool, hinted: float | None) -> RuntimeError:
    if not is_rate_limit:
        return LLMUnavailableError(f"{model} 서버가 혼잡해 응답하지 않습니다(5xx). 잠시 후 다시 시도해주세요.")
    if hinted is not None and hinted > MAX_RETRY_WAIT_SEC:
        return LLMRateLimitError(
            f"{model}의 무료 일일 한도를 다 썼습니다(429). 약 {hinted / 3600:.1f}시간 뒤 풀립니다. "
            "그 전에 쓰려면 .env에서 모델을 바꾸세요."
        )
    return LLMRateLimitError(f"{model}의 무료 한도를 초과했습니다(429). 잠시 후 다시 시도해주세요.")


def _call_with_retry(model: str, messages: list[dict[str, Any]], node: str, kwargs: dict[str, Any]) -> ChatCompletionMessage:
    client = _get_client()
    attempt = 0
    while True:
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        except (RateLimitError, InternalServerError) as exc:
            is_rate_limit = isinstance(exc, RateLimitError)
            hinted = _hinted_wait_sec(exc)
            out_of_retries = attempt >= (MAX_RATE_LIMIT_RETRIES if is_rate_limit else MAX_SERVER_ERROR_RETRIES)
            if out_of_retries or (hinted is not None and hinted > MAX_RETRY_WAIT_SEC):
                raise _give_up_error(model, is_rate_limit, hinted) from exc
            wait = hinted + 1 if hinted is not None else BACKOFF_BASE_SEC * 2**attempt
            logger.warning("[llm] node=%s %s — %.0f초 후 재시도", node, exc.status_code, wait)
            time.sleep(wait)
            attempt += 1
            continue
        _record(node, model, time.perf_counter() - started, response.usage)
        return response.choices[0].message


def chat(messages: list[dict[str, Any]], *, node: str, **kwargs: Any) -> ChatCompletionMessage:
    """모델을 한 번 호출하고 응답 메시지를 돌려준다.

    node: 호출한 노드 이름(로그 집계, 노드별 모델 선택). kwargs는 tools, response_format 등 SDK 인자 그대로.
    """
    model = _model_for(node)
    if llm_cache.enabled():
        cached = llm_cache.load(model, messages, kwargs)
        if cached is not None:
            _record(node, model, 0.0, None, cached=True)
            return cached
    message = _call_with_retry(model, messages, node, kwargs)
    if llm_cache.enabled():
        llm_cache.save(model, messages, kwargs, message)
    return message


def reset_call_log() -> None:
    call_log.clear()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    reply = chat([{"role": "user", "content": "안녕"}], node="hello")
    print(reply.content)
