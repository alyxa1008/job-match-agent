"""구조화 출력: 모델의 JSON 응답을 Pydantic으로 검증한다.

검증에 실패하면 오류 메시지를 모델에 되돌려 1회 재시도한다. 실제 호출은 llm.chat()이 한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from agent import llm

logger = logging.getLogger(__name__)

MAX_JSON_RETRIES = 1

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(llm.LLMError):
    """재시도 후에도 응답이 스키마에 맞지 않음."""


def json_instruction(schema: type[BaseModel]) -> str:
    """시스템 프롬프트에 붙이는 출력 형식 지시문."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return f"아래 JSON Schema를 따르는 JSON 객체 하나만 출력한다. 설명 문장은 쓰지 않는다.\n{schema_json}"


def parse_json(raw: str | None, schema: type[T]) -> T:
    """모델 응답 텍스트를 schema로 검증한다. ```json 코드 블록으로 감싼 응답도 받는다. 실패하면 ValidationError."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    return schema.model_validate_json(text)


def chat_json(messages: list[dict[str, Any]], schema: type[T], *, node: str) -> T:
    """JSON 응답을 schema로 검증해 돌려준다. 검증 실패 시 에러를 모델에 되돌려 1회 재시도."""
    messages = list(messages)
    attempt = 0
    while True:
        reply = llm.chat(messages, node=node, response_format={"type": "json_object"})
        raw = reply.content or ""
        try:
            return parse_json(raw, schema)
        except ValidationError as exc:
            if attempt >= MAX_JSON_RETRIES:
                raise LLMOutputError(f"{node}: 모델 응답이 {schema.__name__} 형식에 맞지 않습니다.") from exc
            logger.warning("[llm] node=%s JSON 검증 실패 — 에러를 되돌려 재시도", node)
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"위 응답이 형식 검증에 실패했다. 오류:\n{exc}\n올바른 JSON 객체만 다시 출력해라."},
            ]
            attempt += 1
