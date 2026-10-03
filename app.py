"""FastAPI 서버: 분석 API + 정적 화면(web/). 내 컴퓨터 안에서만 쓴다(127.0.0.1).

POST /api/analyze              공고 텍스트·캡처 → 진행 단계와 결과를 NDJSON 스트림으로
GET  /api/analyses             기록 목록
GET  /api/analyses/{id}        기록 하나 (리포트 전체)
POST /api/analyses/{id}/decision  지원함/보류/스킵 기록
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import asdict
from typing import Any, Iterator

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import graph, llm, store
from agent.extract import PostingImage
from agent.paths import WEB_DIR
from agent.report import render_report
from agent.schemas import Report

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8000
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 5_000_000
IMAGE_TYPES = {"image/png", "image/jpeg"}

app = FastAPI(title="job-match-agent")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class DecisionBody(BaseModel):
    decision: store.Decision


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


async def _read_image(upload: UploadFile) -> PostingImage:
    if upload.content_type not in IMAGE_TYPES:
        raise HTTPException(400, f"{upload.filename}: png·jpg 파일만 받습니다.")
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"{upload.filename}: 캡처 한 장은 {MAX_IMAGE_BYTES // 1_000_000}MB까지만 받습니다.")
    return PostingImage(data=data, mime_type=upload.content_type)


def _analysis_events(text: str, images: list[PostingImage]) -> Iterator[str]:
    """분석을 별도 스레드에서 돌리고, 노드가 끝날 때마다 한 줄(JSON)씩 내보낸다."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        started = time.perf_counter()
        try:
            with llm.track_calls() as calls:
                for event in graph.run(text, images):
                    if isinstance(event, Report):
                        markdown = render_report(event)
                        analysis_id = store.save_analysis(event, markdown)
                        events.put({"event": "result", "id": analysis_id, "report": event.model_dump(),
                                    "markdown": markdown, "seconds": time.perf_counter() - started,
                                    "llm_calls": sum(1 for record in calls if not record.cached)})
                    else:
                        events.put({"event": "node", **asdict(event)})
        except llm.LLMError as exc:
            events.put({"event": "error", "message": str(exc)})
        except Exception:
            logger.exception("분석 중 오류")
            events.put({"event": "error", "message": "분석 중 오류가 났습니다. 서버 로그를 확인하세요."})
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while (event := events.get()) is not None:
        yield json.dumps(event, ensure_ascii=False) + "\n"


@app.post("/api/analyze")
async def analyze(text: str = Form(""), images: list[UploadFile] = File(default=[])) -> StreamingResponse:
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"캡처는 {MAX_IMAGES}장까지만 받습니다.")
    posting_images = [await _read_image(upload) for upload in images]
    if not text.strip() and not posting_images:
        raise HTTPException(400, "공고 텍스트나 캡처를 넣어주세요.")
    return StreamingResponse(_analysis_events(text, posting_images), media_type="application/x-ndjson")


@app.get("/api/analyses")
def list_analyses() -> list[store.AnalysisSummary]:
    return store.list_analyses()


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: int) -> store.StoredAnalysis:
    stored = store.get_analysis(analysis_id)
    if stored is None:
        raise HTTPException(404, "없는 기록입니다.")
    return stored


@app.post("/api/analyses/{analysis_id}/decision")
def set_decision(analysis_id: int, body: DecisionBody) -> dict[str, Any]:
    if not store.set_decision(analysis_id, body.decision):
        raise HTTPException(404, "없는 기록입니다.")
    return {"id": analysis_id, "decision": body.decision}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run("app:app", host=HOST, port=PORT)
