"""[filters] 하드 조건 판정. LLM 없이 순수 함수로만 한다.

규칙마다 FilterItem 하나를 돌려준다(PASS 포함 — 리포트에 "경력: 연차 조건 없음"처럼 표시하기 위해).
기준값은 모두 profile.yaml에서 온다.
"""

from __future__ import annotations

from datetime import date

from agent.profile import Profile, career_years, format_years
from agent.schemas import FilterItem, FilterStatus, HardFilterResult, JobPosting

WARN_EMPLOYMENT_TYPES = {"계약직", "인턴"}


def _item(rule: str, status: FilterStatus, message: str) -> FilterItem:
    return FilterItem(rule=rule, status=status, message=message)


def check_min_years(posting: JobPosting, my_years: float, tolerance: float) -> FilterItem:
    mine = f"내 경력 {format_years(my_years)}"
    if posting.min_years is None:
        return _item("min_years", "PASS", f"경력: 연차 조건 없음 ({mine})")
    required = f"경력 {posting.min_years:g}년 이상"
    gap = posting.min_years - my_years
    if gap <= 0:
        return _item("min_years", "PASS", f"{required} 충족 ({mine})")
    if gap <= tolerance:
        return _item("min_years", "WARN", f"{required} 조건에 조금 못 미침 ({mine})")
    return _item("min_years", "FAIL", f"{required} 필수 ({mine})")


def check_max_years(posting: JobPosting, my_years: float) -> FilterItem:
    if posting.max_years is None or posting.max_years >= my_years:
        return _item("max_years", "PASS", "경력 상한 조건 없음 또는 충족")
    return _item("max_years", "WARN", f"경력 {posting.max_years:g}년 이하 대상 (내 경력 {format_years(my_years)})")


def check_employment_type(posting: JobPosting, warn_contract: bool) -> FilterItem:
    message = f"고용형태: {posting.employment_type}"
    if warn_contract and posting.employment_type in WARN_EMPLOYMENT_TYPES:
        return _item("employment_type", "WARN", message)
    return _item("employment_type", "PASS", message)


def check_new_grad(posting: JobPosting, warn_new_grad_only: bool) -> FilterItem:
    if warn_new_grad_only and posting.is_new_grad_only:
        return _item("is_new_grad_only", "WARN", "신입 전용 공고 — 신입 처우 가능성")
    return _item("is_new_grad_only", "PASS", "신입 전용 아님")


def check_job_family(posting: JobPosting, avoid_job_families: list[str]) -> FilterItem:
    message = f"직무: {posting.job_family}"
    if posting.job_family in avoid_job_families:
        return _item("job_family", "WARN", f"{message} — 희망 방향과 다름")
    return _item("job_family", "PASS", message)


def check_military_flags(posting: JobPosting, warn_flags: list[str]) -> FilterItem:
    hits = [keyword for keyword in warn_flags if any(keyword in flag for flag in posting.flags)]
    if hits:
        return _item("flags", "WARN", f"{'·'.join(hits)} — 대상자 전용인지 확인 필요")
    return _item("flags", "PASS", "대상 제한 키워드 없음")


def _in_region(location: str, region: str) -> bool:
    """"경기 성남"은 "경기도 성남시 분당구"와 맞는다: 지역 이름의 각 단어가 근무지에 들어 있으면 된다."""
    return all(word in location for word in region.split())


def check_location(posting: JobPosting, ok_regions: list[str], strict: bool) -> FilterItem:
    if posting.location is None:
        return _item("location", "PASS", "근무지: 미기재")
    message = f"근무지: {posting.location}"
    if any(_in_region(posting.location, region) for region in ok_regions):
        return _item("location", "PASS", message)
    return _item("location", "FAIL" if strict else "WARN", f"{message} — 희망 지역 밖")


def _overall(items: list[FilterItem]) -> FilterStatus:
    statuses = {item.status for item in items}
    if "FAIL" in statuses:
        return "FAIL"
    return "WARN" if "WARN" in statuses else "PASS"


def run_filters(posting: JobPosting, profile: Profile, today: date | None = None) -> HardFilterResult:
    my_years = career_years(profile, today)
    rules = profile.rules
    items = [
        check_min_years(posting, my_years, rules.years_tolerance),
        check_max_years(posting, my_years),
        check_employment_type(posting, rules.warn_contract),
        check_new_grad(posting, rules.warn_new_grad_only),
        check_job_family(posting, profile.target.avoid_job_families),
        check_military_flags(posting, rules.warn_military_flags),
        check_location(posting, rules.ok_regions, rules.strict_location),
    ]
    return HardFilterResult(overall=_overall(items), items=items)
