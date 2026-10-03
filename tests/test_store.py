"""SQLite 저장 테스트. 임시 파일 DB를 쓴다."""

import pytest

from agent import store
from agent.schemas import FilterItem, HardFilterResult, Report
from tests.factories import make_posting


def make_report(company: str = "(주)에이블랩", score: int = 4) -> Report:
    filters = HardFilterResult(overall="PASS", items=[FilterItem(rule="min_years", status="PASS", message="통과")])
    return Report(posting=make_posting(company=company), filters=filters, match=None, company=None,
                  score=score, verdict="지원 가능", reasons=["이유"], key_gaps=[], motivation_draft=None)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "test.db"


def test_save_and_get_round_trip(db):
    analysis_id = store.save_analysis(make_report(), "리포트 본문", path=db)
    stored = store.get_analysis(analysis_id, path=db)
    assert stored.id == analysis_id
    assert stored.report.posting.company == "(주)에이블랩"
    assert stored.report_md == "리포트 본문"
    assert stored.decision is None


def test_list_is_newest_first_with_summary_fields(db):
    store.save_analysis(make_report("A사", score=2), "", path=db)
    store.save_analysis(make_report("B사", score=5), "", path=db)
    summaries = store.list_analyses(path=db)
    assert [s.company for s in summaries] == ["B사", "A사"]
    assert summaries[0].score == 5 and summaries[0].verdict == "지원 가능"


def test_set_decision(db):
    analysis_id = store.save_analysis(make_report(), "", path=db)
    assert store.set_decision(analysis_id, "지원함", path=db)
    assert store.get_analysis(analysis_id, path=db).decision == "지원함"
    assert store.list_analyses(path=db)[0].decision == "지원함"


def test_set_decision_on_missing_id_returns_false(db):
    assert not store.set_decision(999, "보류", path=db)


def test_get_missing_returns_none(db):
    assert store.get_analysis(1, path=db) is None
