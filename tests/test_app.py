"""API 테스트. 분석 흐름은 가짜로 바꾸고, 저장은 임시 DB를 쓴다."""

import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
from agent import graph, store
from agent.graph import NodeDone
from agent.schemas import FilterItem, HardFilterResult, Report
from tests.factories import make_posting

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def fake_report() -> Report:
    filters = HardFilterResult(overall="PASS", items=[FilterItem(rule="min_years", status="PASS", message="통과")])
    return Report(posting=make_posting(), filters=filters, match=None, company=None,
                  score=4, verdict="지원 가능", reasons=["이유"], key_gaps=[], motivation_draft="초안")


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    for name in ("save_analysis", "set_decision", "list_analyses", "get_analysis"):
        original = getattr(store, name)
        monkeypatch.setattr(store, name, lambda *args, _f=original, **kwargs: _f(*args, path=db, **kwargs))

    def fake_run(text, images):
        fake_run.received = (text, images)
        yield NodeDone("extract", 0.1, ["filters"])
        yield NodeDone("filters", 0.0, ["match", "research"])
        yield fake_report()

    monkeypatch.setattr(graph, "run", fake_run)
    return TestClient(app_module.app)


def events_of(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines()]


def test_analyze_streams_progress_then_result_and_saves(client):
    response = client.post("/api/analyze", data={"text": "공고 본문"})
    assert response.status_code == 200
    events = events_of(response)
    assert [e["event"] for e in events] == ["node", "node", "result"]
    assert events[0] == {"event": "node", "name": "extract", "seconds": 0.1, "next": ["filters"]}
    result = events[-1]
    assert result["report"]["verdict"] == "지원 가능"
    assert "지원 가능" in result["markdown"]
    assert client.get(f"/api/analyses/{result['id']}").json()["report"]["score"] == 4


def test_analyze_accepts_images(client):
    files = [("images", ("1.png", PNG_HEADER, "image/png")), ("images", ("2.jpg", b"\xff\xd8\xff", "image/jpeg"))]
    response = client.post("/api/analyze", data={"text": ""}, files=files)
    assert response.status_code == 200
    _, images = graph.run.received
    assert [image.mime_type for image in images] == ["image/png", "image/jpeg"]


def test_analyze_rejects_empty_input(client):
    assert client.post("/api/analyze", data={"text": "   "}).status_code == 400


def test_analyze_rejects_non_image_and_oversized_files(client, monkeypatch):
    pdf = [("images", ("a.pdf", b"%PDF", "application/pdf"))]
    assert client.post("/api/analyze", files=pdf).status_code == 400

    monkeypatch.setattr(app_module, "MAX_IMAGE_BYTES", 10)
    big = [("images", ("big.png", b"\x00" * 11, "image/png"))]
    assert client.post("/api/analyze", files=big).status_code == 413


def test_analysis_error_is_reported_as_event(client, monkeypatch):
    from agent import llm

    def failing_run(text, images):
        raise llm.LLMRateLimitError("한도 초과")
        yield  # 제너레이터로 만들기 위한 줄

    monkeypatch.setattr(graph, "run", failing_run)
    events = events_of(client.post("/api/analyze", data={"text": "공고"}))
    assert events == [{"event": "error", "message": "한도 초과"}]


def test_decision_and_history(client):
    result = events_of(client.post("/api/analyze", data={"text": "공고"}))[-1]
    analysis_id = result["id"]
    assert client.post(f"/api/analyses/{analysis_id}/decision", json={"decision": "지원함"}).status_code == 200
    assert client.post(f"/api/analyses/{analysis_id}/decision", json={"decision": "모름"}).status_code == 422
    assert client.post("/api/analyses/999/decision", json={"decision": "보류"}).status_code == 404

    history = client.get("/api/analyses").json()
    assert history[0]["id"] == analysis_id and history[0]["decision"] == "지원함"
    assert client.get("/api/analyses/999").status_code == 404


def test_index_serves_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()
