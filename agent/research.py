"""[research] 회사 조사 — Tool Calling 루프.

모델이 web_search / fetch_page 중 무엇을 쓸지 스스로 고른다. 도구 결과에 실제로 나온 URL만
출처로 인정하고, 출처가 확인되지 않는 정보는 코드가 버린다(추측으로 채우지 않는다).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from agent import llm
from agent.llm_json import chat_json, json_instruction, parse_json
from agent.schemas import CompanyFact, CompanyInfo, JobPosting
from agent.tools import TOOL_SPECS, run_tool

logger = logging.getLogger(__name__)

MAX_LLM_CALLS = 4  # 도구 라운드 최대 3회 + 최종 정리 1회
MAX_TOOL_CALLS_PER_ROUND = 3
NODE = "research"

SYSTEM_PROMPT = """너는 채용공고를 낸 회사를 조사하는 도구다. 지원자가 지원 여부를 판단할 때 볼 사실만 모은다.

찾을 것: 설립 연도, 직원 수, 투자 단계·유치 금액, 주요 제품·사업, 최근 인원 변동(입사·퇴사), 매출 추이, 기업 리뷰 평점.
우선 볼 곳: thevc.kr, innoforest.co.kr, 회사 공식 홈페이지, 뉴스 기사.

진행 방법:
- 도구 호출 기회는 많아야 3번이다. 한 번에 도구를 3개까지 같이 요청할 수 있으니, 첫 기회에 서로 다른 검색 2~3개를 함께 요청한다
  (예: "회사명 투자 유치", "회사명 직원수 매출", "회사명 기업 리뷰 평점"). 비슷한 검색어를 반복하지 않는다.
- 검색 결과가 다른 회사(동명 회사, 업종 불일치)로 보이면 업종 키워드를 붙여 다시 검색한다. 재검색은 최대 2회.
- 스니펫만으로 충분하면 페이지를 가져오지 않는다. 가져오기가 막힌 사이트는 스니펫만 쓴다.
- 필요한 것을 찾았거나 더 찾을 수 없으면 도구를 부르지 말고 최종 JSON을 출력한다.

최종 출력 규칙:
- facts의 각 항목은 한 문장의 사실과, 그 사실이 실제로 적혀 있던 검색 결과·페이지의 URL(source_url)이다.
- 도구 결과에 없는 내용, 다른 회사의 정보, 추측은 쓰지 않는다. 찾지 못했으면 facts를 비우고 found를 false로 한다.
- warnings에는 지원자가 주의할 점만: 인원 급변, 매출 급감, 낮은 평점 등. 근거가 facts에 있어야 한다.
"""

FINAL_REQUEST = "도구는 더 쓸 수 없다. 지금까지의 도구 결과만으로 최종 JSON을 출력해라."
SKIPPED_TOOL_MESSAGE = f"[건너뜀] 한 번에 도구 {MAX_TOOL_CALLS_PER_ROUND}개까지만 실행한다"


class CompanyDraft(BaseModel):
    facts: list[CompanyFact]
    warnings: list[str]
    found: bool


def _first_message(posting: JobPosting) -> str:
    duties = " / ".join(posting.duties[:3]) or "(공고에 없음)"
    return (
        f"회사명: {posting.company}\n공고 제목: {posting.title}\n직무 분류: {posting.job_family}\n"
        f"주요업무: {duties}\n근무지: {posting.location or '(공고에 없음)'}"
    )


def _run_tool_calls(tool_calls: list[Any], seen_urls: set[str]) -> list[dict[str, Any]]:
    """모델이 요청한 도구를 실행해 결과 메시지를 만들고, 결과에 나온 URL을 seen_urls에 더한다."""
    messages = []
    for index, call in enumerate(tool_calls):
        name, arguments = call.function.name, call.function.arguments
        if index < MAX_TOOL_CALLS_PER_ROUND:
            output = run_tool(name, arguments)
            seen_urls |= output.source_urls
            content = output.content
        else:
            content = SKIPPED_TOOL_MESSAGE
        logger.info("[research] %s(%s) → %d자", name, arguments, len(content))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    return messages


def _try_parse(content: str | None) -> CompanyDraft | None:
    try:
        return parse_json(content, CompanyDraft)
    except ValidationError:
        return None


def verify_sources(draft: CompanyDraft, seen_urls: set[str]) -> CompanyInfo:
    """도구 결과에 나온 URL을 출처로 단 사실만 남긴다."""
    facts = [fact for fact in draft.facts if fact.source_url in seen_urls]
    for fact in draft.facts:
        if fact.source_url not in seen_urls:
            logger.warning("[research] 출처를 확인할 수 없어 버림: %r (%s)", fact.text, fact.source_url)
    return CompanyInfo(facts=facts, warnings=draft.warnings if facts else [], found=bool(facts))


def research(posting: JobPosting) -> CompanyInfo:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n{json_instruction(CompanyDraft)}"},
        {"role": "user", "content": _first_message(posting)},
    ]
    seen_urls: set[str] = set()
    for _ in range(MAX_LLM_CALLS - 1):
        reply = llm.chat(messages, node=NODE, tools=TOOL_SPECS)
        messages.append(reply.model_dump(exclude_none=True))
        if not reply.tool_calls:
            draft = _try_parse(reply.content)
            if draft is not None:
                return verify_sources(draft, seen_urls)
            break
        messages += _run_tool_calls(reply.tool_calls, seen_urls)
    messages.append({"role": "user", "content": FINAL_REQUEST})
    return verify_sources(chat_json(messages, CompanyDraft, node=NODE), seen_urls)
