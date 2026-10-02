"""LLM 응답 디스크 캐시 (개발·평가용).

.env에 LLM_CACHE=1을 두면 같은 (모델, 메시지, 옵션) 요청은 저장해 둔 응답을 돌려준다.
프롬프트나 입력이 바뀌면 키가 달라져 자동으로 새로 호출한다. 무료 한도를 테스트 반복에 쓰지 않기 위한 것.
오래된 항목을 지우지 않으므로, 비우려면 data/llm_cache/ 폴더를 삭제한다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessage
from pydantic import BaseModel

from agent.paths import LLM_CACHE_DIR

ENABLED_VALUES = {"1", "true", "on"}


def enabled() -> bool:
    return os.getenv("LLM_CACHE", "").strip().lower() in ENABLED_VALUES


def _jsonable(value: Any) -> Any:
    return value.model_dump() if isinstance(value, BaseModel) else str(value)


def path_for(model: str, messages: list[Any], kwargs: dict[str, Any]) -> Path:
    """요청 내용으로 정해지는 캐시 파일 위치. 이미지가 든 요청은 직렬화가 크므로 한 번만 계산해 load/save에 넘긴다."""
    payload = json.dumps(
        {"model": model, "messages": messages, "kwargs": kwargs},
        sort_keys=True, ensure_ascii=False, default=_jsonable,
    )
    return LLM_CACHE_DIR / f"{hashlib.sha256(payload.encode('utf-8')).hexdigest()}.json"


def load(path: Path) -> ChatCompletionMessage | None:
    if not path.exists():
        return None
    return ChatCompletionMessage.model_validate_json(path.read_text(encoding="utf-8"))


def save(path: Path, message: ChatCompletionMessage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(message.model_dump_json(), encoding="utf-8")
