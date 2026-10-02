"""판단 검증과 리포트 출력 테스트. LLM 호출 없이 가상의 데이터로만 확인한다."""

from agent.judge import JudgeDraft, build_judgement, judge_failed
from agent.report import render_report
from agent.schemas import CompanyFact, CompanyInfo, Evidence, FilterItem, HardFilterResult, MatchResult, Report
from tests.factories import make_posting

POSTING = make_posting(required=["RAG 파이프라인 구축 경험"], preferred=["AI Agent 개발 경험"])
MATCH = MatchResult(
    matched=[Evidence(requirement="RAG 파이프라인 구축 경험", resume_quote="하이브리드 검색을 도입하고 리랭킹 단계를 추가해")],
    gaps=["경력 무관", "AI Agent 개발 경험", "논문 작성 경험"],
    match_ratio=1.0,
)
PASS_FILTERS = HardFilterResult(overall="PASS", items=[
    FilterItem(rule="min_years", status="PASS", message="경력: 연차 조건 없음 (내 경력 2년 3개월)"),
    FilterItem(rule="is_new_grad_only", status="PASS", message="신입 전용 아님"),
])
FAIL_FILTERS = HardFilterResult(overall="FAIL", items=[
    FilterItem(rule="min_years", status="FAIL", message="경력 3년 이상 필수 (내 경력 2년 3개월)"),
    FilterItem(rule="employment_type", status="WARN", message="고용형태: 계약직"),
])


# --- 판단 검증 ---

def test_verdict_comes_from_score():
    assert build_judgement(JudgeDraft(score=5, reasons=[], key_gap_ids=[]), MATCH).verdict == "지원 추천"
    assert build_judgement(JudgeDraft(score=3, reasons=[], key_gap_ids=[]), MATCH).verdict == "보류"
    assert build_judgement(JudgeDraft(score=2, reasons=[], key_gap_ids=[]), MATCH).verdict == "스킵 권장"


def test_score_is_clamped_to_range():
    assert build_judgement(JudgeDraft(score=9, reasons=[], key_gap_ids=[]), MATCH).score == 5
    assert build_judgement(JudgeDraft(score=0, reasons=[], key_gap_ids=[]), MATCH).score == 1


def test_key_gaps_must_come_from_match_gaps():
    draft = JudgeDraft(score=4, reasons=[], key_gap_ids=["G2", "g3", "G2", "G9", "없는 항목"])
    assert build_judgement(draft, MATCH).key_gaps == ["AI Agent 개발 경험", "논문 작성 경험"]


def test_failed_filters_skip_without_model():
    judgement = judge_failed(FAIL_FILTERS)
    assert (judgement.score, judgement.verdict) == (1, "스킵 권장")
    assert judgement.reasons == ["경력 3년 이상 필수 (내 경력 2년 3개월)"]


# --- 리포트 ---

def make_report(**overrides) -> Report:
    base = dict(
        posting=POSTING, filters=PASS_FILTERS, match=MATCH,
        company=CompanyInfo(
            facts=[CompanyFact(text="2024년 설립, 시드 투자 유치", source_url="https://news.example.com/a")],
            warnings=["최근 1년 입사 7 / 퇴사 6"], found=True,
        ),
        score=4, verdict="지원 가능", reasons=["RAG 경험이 직무와 맞음"], key_gaps=["AI Agent 개발 경험"],
        motivation_draft="초안 본문입니다.",
    )
    return Report(**{**base, **overrides})


def test_report_shows_all_sections():
    text = render_report(make_report())
    assert text.splitlines()[:2] == ["(주)에이블랩 · AI/ML 엔지니어", "추천도 ★★★★☆ → 지원 가능"]
    assert "[하드 조건] ✅ 통과" in text
    assert " · 경력: 연차 조건 없음 (내 경력 2년 3개월)" in text
    assert "신입 전용 아님" not in text  # 통과한 부가 조건은 숨긴다
    assert '   근거: "하이브리드 검색을 도입하고 리랭킹 단계를 추가해"' in text
    assert " · AI Agent 개발 경험" in text
    assert " · 2024년 설립, 시드 투자 유치  (출처: https://news.example.com/a)" in text
    assert " · ⚠ 최근 1년 입사 7 / 퇴사 6" in text
    assert text.endswith("[지원동기 초안]\n초안 본문입니다.")


def test_failed_report_is_short():
    report = make_report(filters=FAIL_FILTERS, match=None, company=None, score=1, verdict="스킵 권장",
                         reasons=["경력 3년 이상 필수 (내 경력 2년 3개월)"], key_gaps=[], motivation_draft=None)
    assert render_report(report).splitlines() == [
        "(주)에이블랩 · AI/ML 엔지니어",
        "추천도 ★☆☆☆☆ → 스킵 권장",
        "",
        "[하드 조건] ❌ 조건 미달",
        " ❌ 경력 3년 이상 필수 (내 경력 2년 3개월)",
        " ⚠ 고용형태: 계약직",
    ]


def test_company_not_found_says_no_info():
    report = make_report(company=CompanyInfo(facts=[], warnings=[], found=False), motivation_draft=None)
    assert "[회사]\n · 정보 없음" in render_report(report)
    assert "[지원동기 초안]" not in render_report(report)
