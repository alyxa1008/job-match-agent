"""노드 사이를 오가는 데이터 구조 (CLAUDE.md 4장)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

EmploymentType = Literal["정규직", "계약직", "인턴", "프리랜서", "미기재"]
JobFamily = Literal["AI/ML 엔지니어", "백엔드", "연구", "PM/기획", "보안/컴플라이언스", "영업", "기타"]
FilterStatus = Literal["PASS", "WARN", "FAIL"]
Verdict = Literal["지원 추천", "지원 가능", "보류", "스킵 권장"]


class JobPosting(BaseModel):
    company: str
    title: str
    employment_type: EmploymentType
    is_new_grad_only: bool  # 신입 공채/신입 전용 여부
    min_years: float | None  # "경력 3년 이상" → 3.0, 무관/신입가능 → None
    max_years: float | None  # "경력 5년 이하" → 5.0
    location: str | None
    job_family: JobFamily
    required: list[str]  # 자격요건 항목 원문
    preferred: list[str]  # 우대사항 항목 원문
    duties: list[str]  # 주요업무 항목 원문
    flags: list[str]  # "전문연구요원", "병역특례", "석사졸업예정자" 등 제목/본문 키워드


class FilterItem(BaseModel):
    rule: str  # "min_years", "employment_type", ...
    status: FilterStatus
    message: str


class HardFilterResult(BaseModel):
    overall: FilterStatus
    items: list[FilterItem]


class Evidence(BaseModel):
    requirement: str  # 공고 항목
    resume_quote: str  # resume.md 원문 인용 (코드로 존재 검증)


class MatchResult(BaseModel):
    matched: list[Evidence]
    gaps: list[str]  # 공고 요구 중 이력서에 근거가 없는 것
    match_ratio: float  # 필수요건 중 근거 있는 비율


class CompanyFact(BaseModel):
    text: str
    source_url: str


MetricName = Literal["rating", "review_count", "headcount", "joined_last_year", "left_last_year"]


class CompanyMetric(BaseModel):
    """출처가 있는 숫자. 경고 판정(평점·퇴사 비율)은 이 값으로 코드가 한다."""
    name: MetricName
    value: float
    source_url: str
    as_of: str | None = None  # 출처에 적힌 시점 ("2025년 5월" 등)


class CompanyInfo(BaseModel):
    facts: list[CompanyFact]
    metrics: list[CompanyMetric] = []
    warnings: list[str]  # 코드가 규칙으로 계산: 낮은 평점, 높은 퇴사 비율, 출처마다 다른 수치
    found: bool
    error: str | None = None  # 조사 자체가 실패한 경우의 사유 ("찾았지만 없음"과 구분)


class Report(BaseModel):
    posting: JobPosting
    filters: HardFilterResult
    match: MatchResult | None
    company: CompanyInfo | None
    score: int  # 1~5
    verdict: Verdict
    reasons: list[str]
    key_gaps: list[str]  # match.gaps 중 실제 약점으로 고른 것 (리포트의 [빈 점])
    motivation_draft: str | None
