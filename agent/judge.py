"""[judge] 종합 판단과 [draft] 지원동기 초안.

모델은 추천도(1~5)와 이유, 핵심 빈 점의 번호만 고른다. 결론 문구는 추천도에서 코드가 정하고,
빈 점은 match가 계산한 목록 안에서만 고를 수 있다. 하드 조건 FAIL이면 모델을 부르지 않는다.
"""

from __future__ import annotations

from pydantic import BaseModel

from agent import llm
from agent.llm_json import chat_json, json_instruction
from agent.profile import Profile
from agent.schemas import CompanyInfo, HardFilterResult, JobPosting, MatchResult, Verdict

SCORE_VERDICTS: dict[int, Verdict] = {5: "지원 추천", 4: "지원 가능", 3: "보류", 2: "스킵 권장", 1: "스킵 권장"}
FAIL_SCORE = 1
DRAFT_MIN_SCORE = 3
MAX_KEY_GAPS = 4

JUDGE_PROMPT = """너는 지원자의 입장에서 채용공고에 지원할지 판단하는 도구다. 주어진 자료만 근거로 삼는다.

추천도 기준:
- 5: 필수요건 대부분에 이력서 근거가 있고, 희망 직무와 일치하며, 걸리는 점이 거의 없다.
- 4: 대체로 맞고 빈 점이 있어도 지원하는 데 지장이 없다.
- 3: 맞는 점과 걸리는 점이 비슷하다. 확인이 필요한 조건이 있다.
- 2: 핵심 요건에 근거가 부족하거나 희망 방향과 다르다.
- 1: 지원할 이유가 없다.

규칙:
- 하드 조건의 WARN 항목과 회사 주의사항은 무게를 따져 점수에 반영하고, reasons에도 적는다.
- reasons는 3~5개. 한 항목은 한 문장. 맞는 점을 먼저, 걸리는 점을 나중에 쓴다. 자료에 없는 내용을 지어내지 않는다.
- key_gap_ids에는 [빈 점 후보](G번호) 중 실제로 약점이 되는 것만 최대 4개 고른다.
  "경력 무관" 같은 조건 안내나, 이력서로 확인할 수 없는 일반적인 성향 항목은 고르지 않는다.
"""

DRAFT_PROMPT = """너는 지원자의 지원동기 초안을 쓰는 도구다.

규칙:
- 한국어 존댓말, 5~7문장, 한 단락. 인사말·제목·머리말 없이 본문만 쓴다.
- [이력서 근거]에 있는 경험과 [회사 정보]에 있는 사실만 쓴다. 없는 경험·수치·회사 정보를 지어내지 않는다.
- 공고의 주요업무와 내 경험이 어떻게 이어지는지를 중심으로 쓴다. 과장된 수식어는 쓰지 않는다.
- 빈 점은 굳이 드러내지 않는다.
"""


class JudgeDraft(BaseModel):
    score: int
    reasons: list[str]
    key_gap_ids: list[str]  # "G1", "G2" …


class Judgement(BaseModel):
    score: int
    verdict: Verdict
    reasons: list[str]
    key_gaps: list[str]


def _lines(title: str, lines: list[str]) -> str:
    return f"[{title}]\n" + ("\n".join(lines) if lines else "(없음)")


def _posting_summary(posting: JobPosting) -> str:
    return _lines("공고", [
        f"{posting.company} · {posting.title}",
        f"직무 분류: {posting.job_family} / 고용형태: {posting.employment_type} / 근무지: {posting.location or '미기재'}",
        *[f"주요업무: {duty}" for duty in posting.duties],
    ])


def _evidence_lines(match: MatchResult) -> list[str]:
    return [f'- {evidence.requirement} ← "{evidence.resume_quote}"' for evidence in match.matched]


def _company_lines(company: CompanyInfo) -> list[str]:
    if not company.found:
        return ["정보 없음"]
    return [f"- {fact.text}" for fact in company.facts] + [f"- 주의: {warning}" for warning in company.warnings]


def _number_gaps(match: MatchResult) -> dict[str, str]:
    """빈 점 후보에 G1, G2 … 번호를 붙인다. 모델은 이 번호로만 빈 점을 고른다."""
    return {f"G{index}": gap for index, gap in enumerate(match.gaps, start=1)}


def _judge_message(posting: JobPosting, filters: HardFilterResult, match: MatchResult,
                   company: CompanyInfo, profile: Profile) -> str:
    sections = [
        _posting_summary(posting),
        _lines(f"하드 조건: {filters.overall}", [f"- {item.status} {item.message}" for item in filters.items]),
        _lines(f"이력서 근거가 있는 항목 (필수요건 충족 비율 {match.match_ratio:.0%})", _evidence_lines(match)),
        _lines("빈 점 후보", [f"{gap_id}. {gap}" for gap_id, gap in _number_gaps(match).items()]),
        _lines("회사 정보", _company_lines(company)),
        _lines("지원자 선호", [
            f"희망 직무: {', '.join(profile.target.preferred_job_families)}",
            f"강점 키워드: {', '.join(profile.target.preferred_keywords)}",
            f"약한 분야: {', '.join(profile.target.weak_keywords)}",
        ]),
    ]
    return "\n\n".join(sections)


def build_judgement(draft: JudgeDraft, match: MatchResult) -> Judgement:
    """모델 초안을 검증한다: 점수는 1~5로 자르고, 빈 점은 후보 목록에 있는 번호만 받는다."""
    score = min(max(draft.score, 1), 5)
    gap_ids = _number_gaps(match)
    picked = dict.fromkeys(gap_id.strip().upper() for gap_id in draft.key_gap_ids)
    key_gaps = [gap_ids[gap_id] for gap_id in picked if gap_id in gap_ids][:MAX_KEY_GAPS]
    return Judgement(score=score, verdict=SCORE_VERDICTS[score], reasons=draft.reasons, key_gaps=key_gaps)


def judge_failed(filters: HardFilterResult) -> Judgement:
    """하드 조건 FAIL: 모델을 부르지 않고 바로 스킵 권장."""
    reasons = [item.message for item in filters.items if item.status == "FAIL"]
    return Judgement(score=FAIL_SCORE, verdict=SCORE_VERDICTS[FAIL_SCORE], reasons=reasons, key_gaps=[])


def judge(posting: JobPosting, filters: HardFilterResult, match: MatchResult,
          company: CompanyInfo, profile: Profile) -> Judgement:
    messages = [
        {"role": "system", "content": f"{JUDGE_PROMPT}\n{json_instruction(JudgeDraft)}"},
        {"role": "user", "content": _judge_message(posting, filters, match, company, profile)},
    ]
    return build_judgement(chat_json(messages, JudgeDraft, node="judge"), match)


def write_draft(posting: JobPosting, match: MatchResult, company: CompanyInfo) -> str:
    content = "\n\n".join([
        _posting_summary(posting),
        _lines("이력서 근거", _evidence_lines(match)),
        _lines("회사 정보", _company_lines(company)),
    ])
    reply = llm.chat([{"role": "system", "content": DRAFT_PROMPT}, {"role": "user", "content": content}], node="draft")
    return (reply.content or "").strip()
