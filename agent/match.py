"""[match] 공고 항목과 이력서를 대조한다.

LLM은 항목 번호와 이력서 원문 인용만 고른다. 인용이 이력서에 실제로 있는지, 빈 점이 무엇인지,
충족 비율이 얼마인지는 코드가 판정한다(인용이 없으면 그 항목은 버린다 — 환각 방지).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel

from agent.llm_json import chat_json, json_instruction
from agent.paths import RESUME_PATH
from agent.schemas import Evidence, JobPosting, MatchResult

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 6  # 이보다 짧은 인용은 근거로 보지 않는다
ELLIPSIS = re.compile(r"\.{3,}|…")
MARKDOWN_MARKS = re.compile(r"[*`#>|]")

SYSTEM_PROMPT = """너는 채용공고 항목과 지원자 이력서를 대조하는 도구다.

공고 항목은 번호가 붙어 있다: R=자격요건, P=우대사항, D=주요업무.
이력서에 그 항목을 뒷받침하는 내용이 있을 때만, 항목 번호와 이력서 원문 인용을 짝지어 출력한다.

규칙:
- resume_quote는 이력서에 있는 문장의 일부를 한 글자도 바꾸지 않고 그대로 복사한다. 요약·의역·말줄임(...) 금지.
- 인용은 한 군데에서 이어진 10~80자 정도로, 그 항목과 직접 관련된 부분만 고른다.
- 이력서에 근거가 없는 항목은 출력하지 않는다. 억지로 연결하지 않는다.
- 성향·태도 항목("~한 분")은 이력서에 그것을 보여주는 구체적 사례가 있을 때만 연결한다.
- 항목 하나에 인용은 하나만.
"""


class MatchedItem(BaseModel):
    item_id: str  # "R1", "P2", "D3"
    resume_quote: str


class MatchDraft(BaseModel):
    matched: list[MatchedItem]


def load_resume(path: Path = RESUME_PATH) -> str:
    return path.read_text(encoding="utf-8")


def number_items(posting: JobPosting) -> dict[str, str]:
    """공고 항목에 R1, P1, D1 … 번호를 붙인다."""
    groups = (("R", posting.required), ("P", posting.preferred), ("D", posting.duties))
    return {f"{prefix}{index}": text for prefix, items in groups for index, text in enumerate(items, start=1)}


def _normalize(text: str) -> str:
    return " ".join(MARKDOWN_MARKS.sub("", text).split())


def quote_exists(quote: str, resume: str) -> bool:
    """인용이 이력서 원문에 있는지 확인한다. 공백·마크다운 기호 차이는 무시한다."""
    normalized_resume = _normalize(resume)
    fragments = [_normalize(part) for part in ELLIPSIS.split(quote)]
    fragments = [part for part in fragments if part]
    if not fragments or sum(len(part) for part in fragments) < MIN_QUOTE_CHARS:
        return False
    return all(part in normalized_resume for part in fragments)


def build_result(posting: JobPosting, draft: MatchDraft, resume: str) -> MatchResult:
    """LLM 초안에서 검증된 인용만 남기고, 빈 점과 충족 비율을 계산한다."""
    items = number_items(posting)
    verified: dict[str, Evidence] = {}
    for candidate in draft.matched:
        item_id = candidate.item_id.strip().upper()
        if item_id not in items or item_id in verified:
            continue
        if not quote_exists(candidate.resume_quote, resume):
            logger.warning("[match] 이력서에 없는 인용을 버림: %s %r", item_id, candidate.resume_quote)
            continue
        verified[item_id] = Evidence(requirement=items[item_id], resume_quote=candidate.resume_quote.strip())

    required_ids = [item_id for item_id in items if item_id.startswith("R")]
    gap_ids = [item_id for item_id in items if item_id[0] in "RP" and item_id not in verified]
    matched_required = sum(1 for item_id in required_ids if item_id in verified)
    return MatchResult(
        matched=list(verified.values()),
        gaps=[items[item_id] for item_id in gap_ids],
        match_ratio=matched_required / len(required_ids) if required_ids else 0.0,
    )


def _build_messages(posting: JobPosting, resume: str) -> list[dict[str, str]]:
    numbered = "\n".join(f"{item_id}. {text}" for item_id, text in number_items(posting).items())
    return [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n{json_instruction(MatchDraft)}"},
        {"role": "user", "content": f"[공고 항목]\n{numbered}\n\n[이력서]\n{resume}"},
    ]


def match(posting: JobPosting, resume: str) -> MatchResult:
    if not number_items(posting):
        return MatchResult(matched=[], gaps=[], match_ratio=0.0)
    draft = chat_json(_build_messages(posting, resume), MatchDraft, node="match")
    return build_result(posting, draft, resume)
