"""회사 정보의 출처 신뢰도와 경고 규칙. LLM 없이 코드로 판정한다.

- 출처 등급: 기업 데이터·채용 플랫폼은 trusted, 블로그·개인 사이트·집계 사이트는 low, 그 외(언론 등)는 other.
- low 등급 출처의 사실·수치는 버린다. 같은 수치가 여러 출처에서 나오면 trusted를 고른다.
- 경고는 profile.yaml의 company 규칙으로 계산한다: 낮은 평점, 높은 퇴사 비율, 출처마다 다른 수치.
"""

from __future__ import annotations

import logging
from typing import Iterable, Literal
from urllib.parse import urlsplit

from agent.profile import CompanyRules
from agent.schemas import CompanyFact, CompanyInfo, CompanyMetric, MetricName

logger = logging.getLogger(__name__)

SourceTier = Literal["trusted", "other", "low"]
TIER_ORDER: dict[SourceTier, int] = {"trusted": 0, "other": 1, "low": 2}

TRUSTED_DOMAINS = (
    "thevc.kr", "innoforest.co.kr", "dart.fss.or.kr",  # 기업 데이터
    "jobplanet.co.kr", "teamblind.com",  # 리뷰·평점
    "saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr", "catch.co.kr", "rocketpunch.com",  # 채용 플랫폼 기업정보
)
LOW_TRUST_DOMAINS = (
    "tistory.com", "blog.naver.com", "post.naver.com", "brunch.co.kr", "velog.io", "medium.com",  # 블로그
    "cookiedeal.io", "jstock.com", "grokipedia.com",  # 자동 집계 사이트
)
METRIC_LABELS: dict[MetricName, str] = {
    "rating": "기업 리뷰 평점", "review_count": "리뷰 수", "headcount": "직원 수",
    "joined_last_year": "최근 1년 입사", "left_last_year": "최근 1년 퇴사",
}
METRIC_UNITS: dict[MetricName, str] = {
    "rating": "점", "review_count": "개", "headcount": "명", "joined_last_year": "명", "left_last_year": "명",
}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _in_domains(host: str, domains: Iterable[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def source_tier(url: str) -> SourceTier:
    host = _host(url)
    if _in_domains(host, TRUSTED_DOMAINS):
        return "trusted"
    if _in_domains(host, LOW_TRUST_DOMAINS):
        return "low"
    return "other"


def _usable(source_url: str, seen_urls: set[str], what: str) -> bool:
    """도구 결과에 실제로 나온 URL이고, 신뢰도가 낮은 출처가 아니어야 쓴다."""
    if source_url not in seen_urls:
        logger.warning("[research] 출처를 확인할 수 없어 버림: %s (%s)", what, source_url)
        return False
    if source_tier(source_url) == "low":
        logger.warning("[research] 신뢰도 낮은 출처라 버림: %s (%s)", what, source_url)
        return False
    return True


def format_metric(metric: CompanyMetric) -> str:
    return f"{metric.value:g}{METRIC_UNITS[metric.name]}"


def _pick_metrics(metrics: list[CompanyMetric]) -> tuple[list[CompanyMetric], list[str]]:
    """수치마다 하나만 고른다(trusted 우선). 다른 값을 가진 출처가 더 있으면 경고 문장을 만든다."""
    chosen: list[CompanyMetric] = []
    conflicts: list[str] = []
    for name in METRIC_LABELS:
        candidates = sorted((m for m in metrics if m.name == name), key=lambda m: TIER_ORDER[source_tier(m.source_url)])
        if not candidates:
            continue
        chosen.append(candidates[0])
        others = [m for m in candidates[1:] if m.value != candidates[0].value]
        if others:
            listed = ", ".join(f"{format_metric(m)} ({_host(m.source_url)})" for m in [candidates[0], *others])
            conflicts.append(f"{METRIC_LABELS[name]}가 출처마다 다름: {listed}")
    return chosen, conflicts


def verify(facts: list[CompanyFact], metrics: list[CompanyMetric], seen_urls: set[str]) -> CompanyInfo:
    """모델이 낸 사실·수치 중 출처가 확인되고 신뢰도가 낮지 않은 것만 남긴다. 경고는 아직 계산하지 않는다."""
    kept_facts = [fact for fact in facts if _usable(fact.source_url, seen_urls, fact.text)]
    kept_metrics = [m for m in metrics if _usable(m.source_url, seen_urls, f"{m.name}={m.value:g}")]
    chosen, conflicts = _pick_metrics(kept_metrics)
    return CompanyInfo(facts=kept_facts, metrics=chosen, warnings=conflicts, found=bool(kept_facts or chosen))


def _metric(info: CompanyInfo, name: MetricName) -> CompanyMetric | None:
    return next((m for m in info.metrics if m.name == name), None)


def _source_note(metric: CompanyMetric) -> str:
    as_of = f"{metric.as_of} 기준, " if metric.as_of else ""
    return f"({as_of}출처: {metric.source_url})"


def apply_rules(info: CompanyInfo, rules: CompanyRules) -> CompanyInfo:
    """profile.yaml의 company 규칙으로 경고를 계산해 덧붙인다."""
    warnings = list(info.warnings)
    rating, reviews = _metric(info, "rating"), _metric(info, "review_count")
    if rating and reviews and reviews.value >= rules.min_reviews_for_rating and rating.value < rules.min_rating:
        warnings.append(
            f"기업 리뷰 평점 {rating.value:g}점 (리뷰 {reviews.value:g}개) — 기준 {rules.min_rating:g}점 미만 "
            f"{_source_note(rating)}"
        )
    headcount, left = _metric(info, "headcount"), _metric(info, "left_last_year")
    if headcount and left and headcount.value > 0:
        ratio = left.value / headcount.value
        if ratio >= rules.warn_if_headcount_turnover_ratio:
            joined = _metric(info, "joined_last_year")
            joined_text = f"입사 {joined.value:g} / " if joined else ""
            warnings.append(
                f"최근 1년 {joined_text}퇴사 {left.value:g} — 직원 {headcount.value:g}명 대비 {ratio:.0%} "
                f"(기준 {rules.warn_if_headcount_turnover_ratio:.0%} 이상) {_source_note(left)}"
            )
    return info.model_copy(update={"warnings": warnings})
