"""LLM 호출 단일 진입점.

모델명·base_url·API 키는 .env에서 읽는다. 다른 모듈은 openai SDK를 직접 쓰지 않고
여기의 chat()만 호출한다. 호출마다 노드 이름·소요 시간·토큰 수를 call_log에 남긴다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, TypeVar

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

MAX_RATE_LIMIT_RETRIES = 1
MAX_JSON_RETRIES = 1
BACKOFF_BASE_SEC = 4.0
KEY_PLACEHOLDER_PREFIX = "여기에"

T = TypeVar("T", bound=BaseModel)


class LLMConfigError(RuntimeError):
    """.env 설정이 없거나 잘못됨."""


class LLMRateLimitError(RuntimeError):
    """백오프 후에도 429(한도 초과)."""


class LLMOutputError(RuntimeError):
    """재시도 후에도 응답이 스키마에 맞지 않음."""


@dataclass
class CallRecord:
    node: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int


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
        load_dotenv()
        # SDK 자체 재시도는 끄고, 429 백오프는 chat()에서 직접 제어한다.
        _client = OpenAI(base_url=_env("LLM_BASE_URL"), api_key=_env("LLM_API_KEY"), max_retries=0)
    return _client


def _record(node: str, seconds: float, usage: Any) -> None:
    record = CallRecord(
        node=node,
        seconds=seconds,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )
    call_log.append(record)
    logger.info(
        "[llm] node=%s %.2fs in=%d out=%d",
        record.node, record.seconds, record.prompt_tokens, record.completion_tokens,
    )


def chat(messages: list[dict[str, Any]], *, node: str, **kwargs: Any) -> ChatCompletionMessage:
    """모델을 한 번 호출하고 응답 메시지를 돌려준다.

    node: 호출한 노드 이름(로그 집계용). kwargs는 tools, response_format 등 SDK 인자 그대로.
    """
    client = _get_client()
    model = _env("LLM_MODEL")
    attempt = 0
    while True:
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        except RateLimitError as exc:
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                raise LLMRateLimitError(
                    "LLM 무료 한도를 초과했습니다(429). 잠시 후 다시 시도해주세요."
                ) from exc
            wait = BACKOFF_BASE_SEC * 2**attempt
            logger.warning("[llm] node=%s 429 한도 초과 — %.0f초 후 재시도", node, wait)
            time.sleep(wait)
            attempt += 1
            continue
        _record(node, time.perf_counter() - started, response.usage)
        return response.choices[0].message


def json_instruction(schema: type[BaseModel]) -> str:
    """시스템 프롬프트에 붙이는 출력 형식 지시문."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return f"아래 JSON Schema를 따르는 JSON 객체 하나만 출력한다. 설명 문장은 쓰지 않는다.\n{schema_json}"


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return text


def chat_json(messages: list[dict[str, Any]], schema: type[T], *, node: str) -> T:
    """JSON 응답을 schema로 검증해 돌려준다. 검증 실패 시 에러를 모델에 되돌려 1회 재시도."""
    messages = list(messages)
    attempt = 0
    while True:
        reply = chat(messages, node=node, response_format={"type": "json_object"})
        raw = reply.content or ""
        try:
            return schema.model_validate_json(_strip_code_fence(raw))
        except ValidationError as exc:
            if attempt >= MAX_JSON_RETRIES:
                raise LLMOutputError(f"{node}: 모델 응답이 {schema.__name__} 형식에 맞지 않습니다.") from exc
            logger.warning("[llm] node=%s JSON 검증 실패 — 에러를 되돌려 재시도", node)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"위 응답이 형식 검증에 실패했다. 오류:\n{exc}\n올바른 JSON 객체만 다시 출력해라."},
            ]
            attempt += 1


def reset_call_log() -> None:
    call_log.clear()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    reply = chat([{"role": "user", "content": "안녕"}], node="hello")
    print(reply.content)
