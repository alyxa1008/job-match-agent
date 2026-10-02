"""config/profile.yaml 로딩과 내 경력(연차) 계산.

경력은 재직 기간으로 코드가 계산한다. 숫자를 프롬프트나 코드에 직접 적지 않는다.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "profile.yaml"
DAYS_PER_MONTH = 365.25 / 12
MONTHS_PER_YEAR = 12
FLOAT_EPSILON = 1e-9  # 29/12*12 = 28.999… 같은 부동소수 오차로 한 달이 깎이지 않게
INTERNSHIP_TYPE = "인턴"


class Employment(BaseModel):
    company: str
    start: date
    end: date | None = None  # 재직 중이면 비워 둔다
    type: str


class Me(BaseModel):
    home: str | None = None
    employment: list[Employment]
    include_internship: bool = False


class Target(BaseModel):
    preferred_job_families: list[str] = []
    avoid_job_families: list[str] = []
    preferred_keywords: list[str] = []
    weak_keywords: list[str] = []


class Rules(BaseModel):
    years_tolerance: float = 0.0
    warn_contract: bool = True
    warn_new_grad_only: bool = True
    warn_military_flags: list[str] = []
    ok_regions: list[str] = []
    strict_location: bool = False


class Profile(BaseModel):
    me: Me
    target: Target = Target()
    rules: Rules = Rules()


def load_profile(path: Path = PROFILE_PATH) -> Profile:
    return Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _add_months(day: date, months: int) -> date:
    year, month_index = divmod(day.year * MONTHS_PER_YEAR + day.month - 1 + months, MONTHS_PER_YEAR)
    last_day = calendar.monthrange(year, month_index + 1)[1]
    return date(year, month_index + 1, min(day.day, last_day))


def _months_between(start: date, end: date) -> float:
    """start부터 end(포함)까지의 개월 수. 달력 기준으로 세고 남는 날만 비율로 더한다.

    일수를 365.25로 나누면 "1월 1일~3월 31일"이 3개월에 조금 못 미쳐 한 달 적게 표시된다.
    """
    end_exclusive = end + timedelta(days=1)
    months = (end_exclusive.year - start.year) * MONTHS_PER_YEAR + end_exclusive.month - start.month
    if end_exclusive.day < start.day:
        months -= 1
    leftover_days = (end_exclusive - _add_months(start, months)).days
    return months + leftover_days / DAYS_PER_MONTH


def career_years(profile: Profile, today: date | None = None) -> float:
    """재직 기간 합계(년). 인턴은 include_internship이 true일 때만 포함한다."""
    today = today or date.today()
    months = 0.0
    for job in profile.me.employment:
        if job.type == INTERNSHIP_TYPE and not profile.me.include_internship:
            continue
        months += _months_between(job.start, job.end or today)
    return months / MONTHS_PER_YEAR


def format_years(years: float) -> str:
    """2.28 → "2년 3개월"."""
    months = int(years * MONTHS_PER_YEAR + FLOAT_EPSILON)
    whole_years, rest = divmod(months, MONTHS_PER_YEAR)
    if whole_years and rest:
        return f"{whole_years}년 {rest}개월"
    return f"{whole_years}년" if whole_years else f"{rest}개월"
