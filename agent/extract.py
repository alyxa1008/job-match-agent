"""[extract] 공고 텍스트·캡처 이미지 → JobPosting.

LLM은 공고에 적힌 값을 옮겨 적기만 한다. 판정(경력 비교 등)은 filters.py가 한다.
"""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent.llm import chat_json, json_instruction
from agent.schemas import JobPosting

IMAGE_MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
TEXT_SUFFIXES = {".txt"}

SYSTEM_PROMPT = """너는 채용공고에서 정보를 추출하는 도구다. 공고는 텍스트, 캡처 이미지, 또는 둘 다로 주어진다.
이미지가 여러 장이면 순서대로 이어진 하나의 공고다.

규칙:
- 공고에 적힌 내용만 옮긴다. 적혀 있지 않은 값을 추측해 채우지 않는다.
- company, title: 회사명과 공고 제목 그대로.
- employment_type: 명시가 없으면 "미기재".
- is_new_grad_only: 신입만 모집(신입 공채 등)일 때만 true. "신입/경력", "경력 무관"은 false.
- min_years / max_years: 지원 자격의 경력 연차를 숫자로.
  "경력 3년 이상" → min_years 3.0 / "3~5년" → min_years 3.0, max_years 5.0 / "5년 이하" → max_years 5.0.
  신입 가능, 경력 무관, 언급 없음 → null. 우대사항에만 나오는 연차는 넣지 않는다.
- location: 근무지를 적힌 그대로(시·구 단위까지). 없으면 null.
- job_family: 주요업무를 기준으로 가장 가까운 것 하나.
- required, preferred, duties: 자격요건, 우대사항, 주요업무 항목을 원문 그대로 한 항목씩. 요약·의역하지 않는다. 없으면 빈 배열.
- flags: 제목·본문에 나오는 대상 제한 키워드를 그대로. 예: "전문연구요원", "병역특례", "군대체복무", "산업기능요원", "석사졸업예정자". 없으면 빈 배열.
"""


@dataclass
class PostingImage:
    data: bytes
    mime_type: str

    @classmethod
    def from_path(cls, path: Path) -> PostingImage:
        return cls(data=path.read_bytes(), mime_type=IMAGE_MIME_TYPES[path.suffix.lower()])

    def to_content_part(self) -> dict[str, Any]:
        encoded = base64.b64encode(self.data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{self.mime_type};base64,{encoded}"}}


def load_posting(paths: Sequence[Path]) -> tuple[str, list[PostingImage]]:
    """파일·폴더 경로들에서 공고 텍스트와 이미지를 모은다. 폴더는 파일명 순으로 읽는다."""
    files: list[Path] = []
    for path in paths:
        files += sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]

    texts: list[str] = []
    images: list[PostingImage] = []
    for file in files:
        suffix = file.suffix.lower()
        if suffix in IMAGE_MIME_TYPES:
            images.append(PostingImage.from_path(file))
        elif suffix in TEXT_SUFFIXES:
            texts.append(file.read_text(encoding="utf-8"))
    return "\n\n".join(texts), images


def _build_messages(text: str, images: Sequence[PostingImage]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if text.strip():
        parts.append({"type": "text", "text": f"[공고 텍스트]\n{text.strip()}"})
    if images:
        parts.append({"type": "text", "text": f"[공고 캡처 {len(images)}장]"})
        parts += [image.to_content_part() for image in images]
    return [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n{json_instruction(JobPosting)}"},
        {"role": "user", "content": parts},
    ]


def extract(text: str = "", images: Sequence[PostingImage] = ()) -> JobPosting:
    if not text.strip() and not images:
        raise ValueError("공고 텍스트나 이미지가 필요합니다.")
    return chat_json(_build_messages(text, images), JobPosting, node="extract")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        sys.exit("사용법: python -m agent.extract <공고 파일 또는 폴더> [...]")
    posting_text, posting_images = load_posting([Path(arg) for arg in sys.argv[1:]])
    print(extract(posting_text, posting_images).model_dump_json(indent=2))
