"""LLM 응답 디스크 캐시 (개발·평가용).

.env에 LLM_CACHE=1을 두면 같은 (모델, 메시지, 옵션) 요청은 저장해 둔 응답을 돌려준다.
프롬프트나 입력이 바뀌면 키가 달라져 자동으로 새로 호출한다. 무료 한도를 테스트 반복에 쓰지 않기 위한 것.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessage
from pydantic import BaseModel

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "llm_cache"
ENABLED_VALUES = {"1", "true", "on"}


def enabled() -> bool:
    return os.getenv("LLM_CACHE", "").strip().lower() in ENABLED_VALUES


def _jsonable(value: Any) -> Any:
    return value.model_dump() if isinstance(value, BaseModel) else str(value)


def _path(model: str, messages: list[Any], kwargs: dict[str, Any]) -> Path:
    payload = json.dumps(
        {"model": model, "messages": messages, "kwargs": kwargs},
        sort_keys=True, ensure_ascii=False, default=_jsonable,
    )
    return CACHE_DIR / f"{hashlib.sha256(payload.encode('utf-8')).hexdigest()}.json"


def load(model: str, messages: list[Any], kwargs: dict[str, Any]) -> ChatCompletionMessage | None:
    path = _path(model, messages, kwargs)
    if not path.exists():
        return None
    return ChatCompletionMessage.model_validate_json(path.read_text(encoding="utf-8"))


def save(model: str, messages: list[Any], kwargs: dict[str, Any], message: ChatCompletionMessage) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _path(model, messages, kwargs).write_text(message.model_dump_json(), encoding="utf-8")
