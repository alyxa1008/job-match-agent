"""회사 정보의 출처 신뢰도·수치 선택·경고 규칙 테스트. 전부 코드 판정이라 LLM이 없다."""

from agent.company_rules import apply_rules, source_tier, verify
from agent.profile import CompanyRules
from agent.schemas import CompanyFact, CompanyMetric

THEVC = "https://thevc.kr/ablelab"
NEWS = "https://news.example.com/ablelab"
BLOG = "https://someone.tistory.com/123"
JOBPLANET = "https://www.jobplanet.co.kr/companies/1/reviews/ablelab"
SEEN = {THEVC, NEWS, BLOG, JOBPLANET}


def metric(name, value, url, as_of=None) -> CompanyMetric:
    return CompanyMetric(name=name, value=value, source_url=url, as_of=as_of)


def test_source_tiers():
    assert source_tier(THEVC) == "trusted"
    assert source_tier("https://m.jobkorea.co.kr/Recruit/GI_Read/1") == "trusted"
    assert source_tier(NEWS) == "other"
    assert source_tier(BLOG) == "low"
    assert source_tier("https://blog.naver.com/x/1") == "low"


def test_verify_drops_unseen_and_low_trust_sources():
    facts = [
        CompanyFact(text="2024년 설립", source_url=THEVC),
        CompanyFact(text="직원 500명", source_url="https://made-up.example.com"),
        CompanyFact(text="곧 망한다더라", source_url=BLOG),
    ]
    info = verify(facts, [metric("headcount", 9, BLOG)], SEEN)
    assert [fact.text for fact in info.facts] == ["2024년 설립"]
    assert info.metrics == []
    assert info.found


def test_metric_from_trusted_source_wins_and_conflict_is_reported():
    metrics = [metric("headcount", 9, NEWS), metric("headcount", 25, THEVC), metric("headcount", 25, JOBPLANET)]
    info = verify([], metrics, SEEN)
    assert [(m.name, m.value, m.source_url) for m in info.metrics] == [("headcount", 25, THEVC)]
    assert info.warnings == ["직원 수가 출처마다 다름: 25명 (thevc.kr), 9명 (news.example.com)"]
    assert info.found


def test_same_value_from_two_sources_is_not_a_conflict():
    info = verify([], [metric("headcount", 25, THEVC), metric("headcount", 25, NEWS)], SEEN)
    assert info.warnings == []


def test_nothing_usable_means_not_found():
    info = verify([CompanyFact(text="x", source_url=BLOG)], [], SEEN)
    assert not info.found and info.facts == []


# --- profile.yaml 규칙 ---

RULES = CompanyRules(min_rating=3.0, min_reviews_for_rating=5, warn_if_headcount_turnover_ratio=0.4)


def test_low_rating_warns_only_with_enough_reviews():
    few = verify([], [metric("rating", 2.5, JOBPLANET), metric("review_count", 3, JOBPLANET)], SEEN)
    assert apply_rules(few, RULES).warnings == []

    enough = verify([], [metric("rating", 2.5, JOBPLANET, as_of="2026년 9월"), metric("review_count", 12, JOBPLANET)], SEEN)
    [warning] = apply_rules(enough, RULES).warnings
    assert warning.startswith("기업 리뷰 평점 2.5점 (리뷰 12개) — 기준 3점 미만 (2026년 9월 기준, 출처: ")


def test_good_rating_does_not_warn():
    info = verify([], [metric("rating", 3.8, JOBPLANET), metric("review_count", 40, JOBPLANET)], SEEN)
    assert apply_rules(info, RULES).warnings == []


def test_high_turnover_warns():
    metrics = [metric("headcount", 10, THEVC), metric("joined_last_year", 7, THEVC), metric("left_last_year", 6, THEVC)]
    [warning] = apply_rules(verify([], metrics, SEEN), RULES).warnings
    assert warning.startswith("최근 1년 입사 7 / 퇴사 6 — 직원 10명 대비 60% (기준 40% 이상)")


def test_low_turnover_does_not_warn():
    metrics = [metric("headcount", 50, THEVC), metric("left_last_year", 5, THEVC)]
    assert apply_rules(verify([], metrics, SEEN), RULES).warnings == []


def test_rules_keep_conflict_warnings():
    metrics = [metric("headcount", 10, THEVC), metric("headcount", 30, NEWS), metric("left_last_year", 6, THEVC)]
    warnings = apply_rules(verify([], metrics, SEEN), RULES).warnings
    assert len(warnings) == 2
    assert warnings[0].startswith("직원 수가 출처마다 다름")
