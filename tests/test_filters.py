"""하드 필터 규칙 테스트. 가상의 프로필·공고만 쓴다(개인 데이터 파일을 읽지 않는다)."""

from datetime import date
from pathlib import Path

import pytest

from agent.filters import run_filters
from agent.profile import Employment, Me, Profile, Rules, Target, career_years, format_years, load_profile
from agent.schemas import JobPosting

EXAMPLE_PROFILE = Path(__file__).resolve().parent.parent / "config" / "profile.example.yaml"


def make_profile(**rule_overrides) -> Profile:
    """정규직 2년 3개월 + 인턴 2개월인 가상의 지원자."""
    rules = dict(
        years_tolerance=0,
        warn_military_flags=["전문연구요원", "병역특례", "군대체복무"],
        ok_regions=["서울", "경기 성남", "판교"],
    )
    return Profile(
        me=Me(
            employment=[
                Employment(company="예시테크", start=date(2024, 1, 1), end=date(2026, 3, 31), type="정규직"),
                Employment(company="샘플랩스", start=date(2023, 7, 1), end=date(2023, 8, 31), type="인턴"),
            ],
        ),
        target=Target(avoid_job_families=["PM/기획", "보안/컴플라이언스", "영업"]),
        rules=Rules(**{**rules, **rule_overrides}),
    )


def make_posting(**overrides) -> JobPosting:
    """아무 조건에도 걸리지 않는 공고."""
    base = dict(
        company="(주)에이블랩", title="AI/ML 엔지니어", employment_type="정규직", is_new_grad_only=False,
        min_years=None, max_years=None, location="서울 관악구", job_family="AI/ML 엔지니어",
        required=[], preferred=[], duties=[], flags=[],
    )
    return JobPosting(**{**base, **overrides})


def status_of(result, rule: str) -> str:
    return next(item.status for item in result.items if item.rule == rule)


def message_of(result, rule: str) -> str:
    return next(item.message for item in result.items if item.rule == rule)


# --- 경력 계산 ---

def test_career_years_excludes_internship_by_default():
    assert format_years(career_years(make_profile())) == "2년 3개월"


def test_career_years_includes_internship_when_enabled():
    profile = make_profile()
    profile.me.include_internship = True
    assert format_years(career_years(profile)) == "2년 5개월"


def test_career_years_uses_today_for_current_job():
    profile = Profile(me=Me(employment=[Employment(company="예시테크", start=date(2025, 1, 1), type="정규직")]))
    assert format_years(career_years(profile, today=date(2025, 12, 31))) == "1년"


def test_example_profile_loads():
    profile = load_profile(EXAMPLE_PROFILE)
    assert format_years(career_years(profile)) == "2년"


# --- 깨끗한 공고 ---

def test_clean_posting_passes():
    result = run_filters(make_posting(), make_profile())
    assert result.overall == "PASS"
    assert message_of(result, "min_years") == "경력: 연차 조건 없음 (내 경력 2년 3개월)"


# --- 경력 하한 ---

def test_min_years_above_career_fails():
    result = run_filters(make_posting(company="(주)비전웍스", min_years=3.0), make_profile())
    assert result.overall == "FAIL"
    assert message_of(result, "min_years") == "경력 3년 이상 필수 (내 경력 2년 3개월)"


def test_min_years_met_passes():
    assert status_of(run_filters(make_posting(min_years=2.0), make_profile()), "min_years") == "PASS"


@pytest.mark.parametrize(
    ("tolerance", "expected"),
    [(0, "FAIL"), (0.5, "WARN")],
)
def test_min_years_slightly_short_depends_on_tolerance(tolerance, expected):
    # 2년 3개월 vs 2.5년 → 3개월 부족
    result = run_filters(make_posting(min_years=2.5), make_profile(years_tolerance=tolerance))
    assert status_of(result, "min_years") == expected


def test_min_years_far_short_fails_even_with_tolerance():
    result = run_filters(make_posting(min_years=5.0), make_profile(years_tolerance=0.5))
    assert status_of(result, "min_years") == "FAIL"


# --- 경고 규칙 ---

def test_max_years_below_career_warns():
    result = run_filters(make_posting(max_years=2.0), make_profile())
    assert status_of(result, "max_years") == "WARN"
    assert result.overall == "WARN"


@pytest.mark.parametrize("employment_type", ["계약직", "인턴"])
def test_contract_or_intern_warns(employment_type):
    result = run_filters(make_posting(employment_type=employment_type), make_profile())
    assert status_of(result, "employment_type") == "WARN"


def test_contract_passes_when_warning_disabled():
    result = run_filters(make_posting(employment_type="계약직"), make_profile(warn_contract=False))
    assert status_of(result, "employment_type") == "PASS"


def test_new_grad_only_warns():
    assert status_of(run_filters(make_posting(is_new_grad_only=True), make_profile()), "is_new_grad_only") == "WARN"


def test_avoided_job_family_warns():
    result = run_filters(make_posting(job_family="PM/기획"), make_profile())
    assert status_of(result, "job_family") == "WARN"


def test_military_flag_warns():
    posting = make_posting(flags=["석사졸업예정자", "전문연구요원"])
    result = run_filters(posting, make_profile())
    assert status_of(result, "flags") == "WARN"
    assert "전문연구요원" in message_of(result, "flags")


def test_unrelated_flag_passes():
    assert status_of(run_filters(make_posting(flags=["석사졸업예정자"]), make_profile()), "flags") == "PASS"


# --- 근무지 ---

@pytest.mark.parametrize("location", ["서울 강남구 남부순환로351길 19", "경기도 성남시 분당구", "경기 성남시 분당구 판교"])
def test_location_in_ok_regions_passes(location):
    assert status_of(run_filters(make_posting(location=location), make_profile()), "location") == "PASS"


def test_location_outside_warns_by_default():
    assert status_of(run_filters(make_posting(location="울산 남구"), make_profile()), "location") == "WARN"


def test_location_outside_fails_when_strict():
    result = run_filters(make_posting(location="울산 남구"), make_profile(strict_location=True))
    assert result.overall == "FAIL"


def test_missing_location_passes():
    assert status_of(run_filters(make_posting(location=None), make_profile()), "location") == "PASS"


# --- 종합 ---

def test_fail_outranks_warn():
    posting = make_posting(min_years=3.0, employment_type="계약직", job_family="보안/컴플라이언스")
    result = run_filters(posting, make_profile())
    assert result.overall == "FAIL"
    assert status_of(result, "employment_type") == "WARN"
