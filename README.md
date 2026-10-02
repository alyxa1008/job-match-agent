# job-match-agent — 채용공고 매칭 Agent

채용공고 텍스트나 캡처 이미지를 넣으면, 내 이력서·기준과 대조해 **지원 여부를 판단하고 근거·회사 정보·지원동기 초안까지 만들어 주는 개인용 AI Agent**입니다.

> **상태: 개발 진행 중.** 지금은 공고 추출 단계까지 구현돼 있고, 아래 [진행 상황](#진행-상황)에 단계별 현황을 적어 둡니다. 평가 수치는 평가 단계(M6)에서 채웁니다.

## 왜 만들었나

공고 하나를 판단하는 데 10분 이상 걸립니다. 자격요건을 이력서와 대조하고, 경력 조건을 확인하고, 회사를 조사해야 하기 때문입니다. 게다가 판단 기준이 그날그날 흔들립니다. 이 과정을 20초 안팎으로, 매번 같은 기준으로 처리하는 것이 목표입니다.

## 동작 흐름

```
입력: 공고 텍스트 및/또는 캡처 이미지(jpg·png)
  │
  ▼
[extract]   LLM 구조화 출력(이미지는 멀티모달 입력) → JobPosting(JSON)
  │
  ▼
[filters]   순수 파이썬 규칙 → HardFilterResult (PASS / WARN / FAIL)
  │
  ├─ FAIL → [report] 로 바로 이동 (회사 조사·매칭 생략: 호출·시간 절약)
  │
  ▼ PASS / WARN
[match] ─┐  병렬 실행
[research]┘  research는 web_search / fetch_page 도구를 고르는 Tool Calling 루프
  │
  ▼
[judge]     종합 → 추천도(1~5), 결론, 맞는 점/빈 점/걸리는 점
  │
  ├─ 추천도 ≥ 3 → [draft] 지원동기 초안
  ▼
[report]    Markdown 리포트 + DB 저장
```

리포트 예시(가상의 회사):

```
(주)에이블랩 · AI/ML 엔지니어
추천도 ★★★★★ → 지원 추천

[하드 조건] ✅ 통과
 · 경력: 연차 조건 없음 (내 경력 2년 3개월)
 · 고용형태: 정규직

[맞는 점]
 · RAG 파이프라인(chunking·retriever·reranker·평가)
   근거: "하이브리드 검색을 도입해 ... 개선"   ← 이력서 원문 인용
[빈 점]
 · AI Agent 실무 경험 없음
[회사]
 · 2024년 설립, 약 10명, 시드  (출처: URL)
[지원동기 초안]  (추천도 ★★★ 이상일 때만 생성)
```

## 설계 원칙

1. **숫자·규칙 판정은 LLM이 아니라 코드가 한다.** 경력 연차 비교, 고용형태, 키워드 경고는 순수 함수(`filters.py`)로 처리하고 pytest로 검증합니다. LLM은 공고에서 값을 추출만 합니다.
2. **모든 "맞는 점"에는 이력서 원문 인용을 근거로 단다.** 인용이 이력서에 실제로 있는지 코드로 확인하고, 없으면 그 항목을 버립니다(환각 방지).
3. **회사 정보는 반드시 출처 URL과 함께.** 찾지 못하면 "정보 없음"이라고 쓰고 추측으로 채우지 않습니다.
4. **Agent다운 분기.** 하드 조건 FAIL이면 조사를 생략하고, 회사명이 흔해 검색 결과가 어긋나면 업종 키워드를 붙여 재검색하며, 추천도가 낮으면 초안을 만들지 않습니다.
5. **모델 교체 가능.** LLM 호출은 `agent/llm.py` 한 곳에서만 하고, 모델명·엔드포인트는 환경변수로 둡니다.
6. **이력서는 RAG 없이 통째로 컨텍스트에 넣는다.** 5쪽 분량이라 검색 단계를 둘 이유가 없습니다.

## 기술 스택

| 역할 | 선택 |
|---|---|
| 언어 | Python 3.11+ |
| LLM | Google Gemini Flash (OpenAI 호환 엔드포인트, `openai` SDK) |
| 구조화 출력 | Pydantic 검증 + 실패 시 오류를 모델에 되돌려 1회 재시도 |
| Agent | 직접 구현한 Tool Calling 루프 → LangGraph로 이전 |
| 검색 | DuckDuckGo 검색 + 본문 추출 |
| 저장 | SQLite |
| 화면 | FastAPI + 정적 HTML/CSS/JS (로컬 전용, 진행 단계 SSE 스트리밍) |
| 테스트 | pytest |

## 실행 방법

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env                          # LLM_API_KEY 입력 (Google AI Studio 무료 키)
cp config/profile.example.yaml config/profile.yaml
cp data/resume.example.md data/resume.md      # 자기 이력서로 교체

.venv/bin/python -m agent.llm                             # 모델 호출 확인
.venv/bin/python -m agent.extract 공고.png                 # 공고 → JobPosting JSON
.venv/bin/python -m agent.extract 공고1.png 공고2.png 메모.txt   # 여러 장 + 텍스트
```

## 진행 상황

| 단계 | 내용 | 상태 |
|---|---|---|
| M0 | 저장소 뼈대, LLM 호출 단일 진입점(`llm.py`: 429 백오프, 노드별 시간·토큰 로깅) | 구현 · 실호출 검증 중 |
| M1 | 데이터 구조(`schemas.py`), 공고 추출(`extract.py`: 텍스트·이미지 입력) | 구현 · 실호출 검증 중 |
| M2 | 하드 필터(`filters.py`) + 단위 테스트 | 예정 |
| M3 | 매칭, 회사 조사(Tool Calling 루프), 판단, 리포트 | 예정 |
| M4 | LangGraph 이전 (조기 종료·병렬·조건 분기) | 예정 |
| M5 | SQLite 저장 + FastAPI 웹 화면 | 예정 |
| M6 | 평가 스크립트: 직접 판단한 공고 라벨 대비 판단 일치율, 하드 필터 정확도, 평균 응답 시간·LLM 호출 수 | 예정 |

## 개인 데이터

이력서(`data/resume.md`), 판단 기준(`config/profile.yaml`), 평가 라벨과 공고 원문(`eval/labels.csv`, `eval/cases/`)은 저장소에 올리지 않습니다. 형식은 각 위치의 `*.example` 파일로 확인할 수 있습니다. 채용 사이트를 크롤링하지 않으며, 공고는 사용자가 직접 넣은 텍스트·캡처만 사용합니다.
