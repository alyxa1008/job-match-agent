"""인용 검증과 매칭 결과 계산 테스트. LLM 호출 없이 가상의 이력서로만 확인한다."""

from agent.match import MatchDraft, MatchedItem, build_result, number_items, quote_exists
from agent.schemas import JobPosting
from tests import factories

RESUME = """## 경력기술서

**사내 문서 검색 챗봇 (RAG)**
- 키워드 검색과 벡터 검색을 결합한 하이브리드 검색을 도입하고 리랭킹 단계를 추가해 무관한 문서가 검색되는 문제를 개선
- Python·FastAPI 기반 API 설계 및 개발,
  PostgreSQL 데이터 모델링
"""


def make_posting() -> JobPosting:
    return factories.make_posting(
        required=["RAG 파이프라인 구축 경험", "Python 백엔드 개발 경험"],
        preferred=["AI Agent 개발 경험"],
        duties=["검색 품질 개선"],
    )


def draft(*pairs: tuple[str, str]) -> MatchDraft:
    return MatchDraft(matched=[MatchedItem(item_id=item_id, resume_quote=quote) for item_id, quote in pairs])


# --- 인용 검증 ---

def test_exact_quote_exists():
    assert quote_exists("하이브리드 검색을 도입하고 리랭킹 단계를 추가해", RESUME)


def test_quote_ignores_markdown_and_line_breaks():
    assert quote_exists("사내 문서 검색 챗봇 (RAG)", RESUME)
    assert quote_exists("API 설계 및 개발, PostgreSQL 데이터 모델링", RESUME)


def test_paraphrased_quote_is_rejected():
    assert not quote_exists("하이브리드 검색을 적용해 검색 품질을 높임", RESUME)


def test_ellipsis_quote_needs_every_fragment():
    assert quote_exists("하이브리드 검색을 도입하고 ... 문제를 개선", RESUME)
    assert not quote_exists("하이브리드 검색을 도입하고 ... 매출을 개선", RESUME)


def test_too_short_quote_is_rejected():
    assert not quote_exists("RAG", RESUME)
    assert not quote_exists("...", RESUME)


# --- 결과 계산 ---

def test_number_items():
    assert list(number_items(make_posting())) == ["R1", "R2", "P1", "D1"]


def test_verified_quote_is_kept_and_ratio_counts_required_only():
    result = build_result(
        make_posting(),
        draft(("R1", "하이브리드 검색을 도입하고 리랭킹 단계를 추가해"), ("D1", "무관한 문서가 검색되는 문제를 개선")),
        RESUME,
    )
    assert [evidence.requirement for evidence in result.matched] == ["RAG 파이프라인 구축 경험", "검색 품질 개선"]
    assert result.match_ratio == 0.5
    assert result.gaps == ["Python 백엔드 개발 경험", "AI Agent 개발 경험"]


def test_fabricated_quote_is_dropped_and_becomes_gap():
    result = build_result(make_posting(), draft(("P1", "LangGraph로 멀티 에이전트 시스템을 구축")), RESUME)
    assert result.matched == []
    assert "AI Agent 개발 경험" in result.gaps
    assert result.match_ratio == 0.0


def test_unknown_or_duplicate_item_ids_are_ignored():
    quote = "Python·FastAPI 기반 API 설계 및 개발"
    result = build_result(make_posting(), draft(("R9", quote), ("r2", quote), ("R2", quote)), RESUME)
    assert len(result.matched) == 1
    assert result.matched[0].requirement == "Python 백엔드 개발 경험"


def test_duties_are_not_counted_as_gaps():
    result = build_result(make_posting(), draft(), RESUME)
    assert "검색 품질 개선" not in result.gaps


def test_posting_without_required_has_zero_ratio():
    posting = make_posting().model_copy(update={"required": []})
    assert build_result(posting, draft(), RESUME).match_ratio == 0.0
