# CLAUDE.md — 채용공고 매칭 Agent (job-match-agent)

이 문서는 이 저장소에서 작업하는 AI 코딩 도우미(Claude)를 위한 프로젝트 설명서다.
작업을 시작하기 전에 끝까지 읽고, 여기 적힌 범위·규칙·단계 순서를 지킨다.

---

## 1. 무엇을 만드나

**한 줄 요약:** 채용공고 텍스트나 캡처 이미지(jpg·png)를 넣으면, 내 이력서·기준과 대조해 **지원 여부를 판단하고 근거·회사 정보·지원동기 초안까지 만들어주는 개인용 AI Agent.**

- **사용자:** 개발자 본인 1명. AI/LLM 엔지니어로 구직 중이며 매일 사람인 공고 2개 안팎을 검토·지원한다.
- **해결하는 문제:** 공고 하나를 판단하는 데 10분 이상 걸리고(요건 대조, 경력 조건 확인, 회사 조사), 기준이 매번 흔들린다. 이걸 20초 안팎으로, 같은 기준으로 처리한다.
- **두 가지 목표**
  1. **실용:** 매일 실제로 쓴다.
  2. **포트폴리오:** 이력서에 없는 **AI Agent(Tool Calling, LangGraph) 실무 경험**을 증명한다. 그래서 설계 이유와 평가 수치를 README에 남기는 것이 결과물만큼 중요하다.

---

## 2. 사용 흐름 (사용자 관점)

1. `uvicorn app:app`으로 띄운 로컬 웹 화면(`localhost:8000`)에 공고 텍스트를 붙여넣거나 캡처 파일(jpg·png, 여러 장 가능)을 올리고 [분석]을 누른다. 사람인 공고는 이미지형이 많아 캡처 입력이 기본 경로다.
2. 진행 단계(추출 → 하드필터 → 매칭·회사 조사 → 판단 → 초안)가 실시간으로 표시되고, 10~20초 뒤 리포트가 나온다.
3. 리포트 아래 [지원함] [보류] [스킵] 버튼으로 내 결정을 기록한다.
4. [기록] 탭에서 지금까지 분석·결정한 공고 목록을 본다.

### 리포트 예시 (출력 형태의 기준)

```
(주)에이블랩 · AI/ML 엔지니어
추천도 ★★★★★ → 지원 추천

[하드 조건] ✅ 통과
 · 경력: 연차 조건 없음 (내 경력 2년 3개월)
 · 고용형태: 정규직
 · 근무지: 서울 관악구

[맞는 점]
 · RAG 파이프라인(chunking·retriever·reranker·평가)
   근거: "하이브리드 검색을 도입해 ... 개선"   ← resume.md 원문 인용
[빈 점]
 · AI Agent 실무 경험 없음
[회사]
 · 2024년 설립, 약 10명, 시드  (출처: thevc.kr/...)
 · ⚠ 최근 1년 입사 7 / 퇴사 6
[지원동기 초안]  (추천도 ★★★ 이상일 때만 생성)
```

```
(주)비전웍스 · 데이터 분석 AI 엔지니어
추천도 ★☆☆☆☆ → 스킵 권장
[하드 조건] ❌ 경력 3년 이상 필수 (내 경력 2년 3개월)
```

---

## 3. 아키텍처

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
[research]┘
  │
  ▼
[judge]     종합 → 추천도(1~5), 결론, 맞는 점/빈 점/걸리는 점
  │
  ├─ 추천도 ≥ 3 → [draft] 지원동기 초안
  ▼
[report]    Markdown 리포트 + DB 저장
```

### 설계 원칙 (README에도 그대로 쓴다)

1. **숫자·규칙 판정은 LLM이 아니라 코드가 한다.** 경력 연차 비교, 고용형태, 키워드 경고는 `filters.py`의 순수 함수. LLM은 공고에서 값을 *추출*만 한다.
2. **모든 "맞는 점"은 resume.md 원문 인용을 근거로 단다.** 인용이 resume.md에 실제로 존재하는지 코드로 검증하고, 없으면 그 항목을 버린다(환각 방지).
3. **회사 정보는 반드시 출처 URL과 함께.** 찾지 못하면 "정보 없음"이라고 쓴다. 추측으로 채우지 않는다.
4. **Agent다운 분기:** 하드 조건 FAIL이면 조사 생략 / 회사명이 흔하면(검색 결과에 업종이 안 맞음) 업종 키워드를 붙여 재검색(최대 2회) / 추천도 낮으면 초안 생략.
5. **모델 교체 가능:** LLM 호출은 `agent/llm.py` 한 곳에서만. 모델명·base_url은 환경변수.
6. **이력서는 RAG 없이 통째로 컨텍스트에 넣는다.** 5쪽 분량이라 충분히 들어간다. (README에 "필요 없어서 안 썼다"는 판단 근거를 남긴다.)

---

## 4. 데이터 구조 (Pydantic 모델로 정의)

```python
class JobPosting(BaseModel):
    company: str
    title: str
    employment_type: Literal["정규직", "계약직", "인턴", "프리랜서", "미기재"]
    is_new_grad_only: bool            # 신입 공채/신입 전용 여부
    min_years: float | None           # "경력 3년 이상" → 3.0, 무관/신입가능 → None
    max_years: float | None           # "경력 5년 이하" → 5.0
    location: str | None
    job_family: Literal["AI/ML 엔지니어", "백엔드", "연구", "PM/기획", "보안/컴플라이언스", "영업", "기타"]
    required: list[str]               # 자격요건 항목 원문
    preferred: list[str]              # 우대사항 항목 원문
    duties: list[str]                 # 주요업무 항목 원문
    flags: list[str]                  # "전문연구요원", "병역특례", "석사졸업예정자" 등 제목/본문 키워드

class FilterItem(BaseModel):
    rule: str                         # "min_years", "employment_type", ...
    status: Literal["PASS", "WARN", "FAIL"]
    message: str

class HardFilterResult(BaseModel):
    overall: Literal["PASS", "WARN", "FAIL"]
    items: list[FilterItem]

class Evidence(BaseModel):
    requirement: str                  # 공고 항목
    resume_quote: str                 # resume.md 원문 인용 (코드로 존재 검증)

class MatchResult(BaseModel):
    matched: list[Evidence]
    gaps: list[str]                   # 공고 요구 중 이력서에 근거가 없는 것
    match_ratio: float                # 필수요건 중 근거 있는 비율

class CompanyFact(BaseModel):
    text: str
    source_url: str

class CompanyInfo(BaseModel):
    facts: list[CompanyFact]
    warnings: list[str]               # 인원 급변, 매출 급감 등
    found: bool

class Report(BaseModel):
    posting: JobPosting
    filters: HardFilterResult
    match: MatchResult | None
    company: CompanyInfo | None
    score: int                        # 1~5
    verdict: Literal["지원 추천", "지원 가능", "보류", "스킵 권장"]
    reasons: list[str]
    motivation_draft: str | None
```

---

## 5. 하드 필터 규칙 (`agent/filters.py`, 순수 함수 + pytest)

설정값은 `config/profile.yaml`에서 읽는다. 하드코딩 금지.

| 규칙 | 판정 |
|---|---|
| `min_years` > 내 경력(년) | **FAIL** — "경력 N년 이상 필수 (내 경력 X년 Y개월)" |
| `min_years` > 내 경력 − 0.5 이내로 근소 초과 | 위 FAIL 대신 **WARN** 으로 할지: 설정값 `years_tolerance`로 제어 (기본 0 → FAIL) |
| `max_years` < 내 경력 | WARN |
| `employment_type` == 계약직/인턴 | WARN |
| `is_new_grad_only` | WARN — "신입 처우 가능성" |
| `job_family` ∈ 설정의 `avoid_job_families` | WARN — "희망 방향과 다름" |
| `flags`에 전문연구요원/병역특례 | WARN — "대상자 전용인지 확인 필요" |
| `location`이 설정의 `ok_regions`에 없음 | WARN (기본), 설정 `strict_location: true`면 FAIL |

`overall` = 하나라도 FAIL이면 FAIL, 아니면 WARN이 있으면 WARN, 아니면 PASS.

내 경력은 `profile.yaml`의 재직 기간으로 **코드가 계산**한다(정규직만, 인턴 제외 옵션).

---

## 6. 도구 (Tool Calling 대상)

`research` 노드에서 LLM이 아래 도구를 골라 쓰는 **Tool Calling 루프**로 구현한다 (최대 반복 6회).

| 도구 | 설명 |
|---|---|
| `web_search(query: str) -> list[{title, url, snippet}]` | DuckDuckGo 검색 라이브러리 사용 (키 불필요). 패키지명은 설치 시 최신 이름 확인 (`ddgs` 또는 `duckduckgo-search`) |
| `fetch_page(url: str) -> str` | httpx로 가져와 본문 텍스트만 추출(최대 8,000자). robots.txt 차단·로그인 필요 페이지는 시도하지 않는다 |

- 우선 확인할 소스: thevc.kr, innoforest.co.kr, 회사 공식 홈페이지, 뉴스 기사.
- 잡플래닛 등 로그인/차단 페이지는 검색 결과 스니펫까지만 사용.
- **사람인·잡플래닛 등을 크롤링하지 않는다.** 공고는 사용자가 직접 넣은 텍스트·캡처 이미지만 쓴다.

---

## 7. 기술 스택

| 역할 | 선택 |
|---|---|
| 언어 | Python 3.11+ |
| LLM | Google Gemini 무료 API (Flash 계열). **OpenAI 호환 엔드포인트**로 호출해 `openai` SDK 하나로 통일. base_url·모델명은 `.env` (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`). 정확한 엔드포인트와 현재 모델명은 Google AI Studio 문서에서 확인 후 기입 |
| 구조화 출력 | Pydantic + JSON 응답 (실패 시 1회 재시도, 에러 메시지를 모델에 되돌려줌) |
| Agent | **M3까지는 직접 짠 루프**, M4에서 **LangGraph**로 이전 |
| 검색 | DuckDuckGo 검색 라이브러리 + httpx + 본문 추출(trafilatura 또는 BeautifulSoup) |
| 저장 | SQLite (`data/app.db`) — 분석 결과와 내 결정 |
| 화면 | FastAPI + 정적 HTML/CSS/JS 한 벌(빌드 도구 없음). `127.0.0.1`에만 바인딩하는 내부용. 분석 진행 단계는 SSE로 실시간 전송 |
| 테스트 | pytest (filters는 필수) |
| 배포 | 없음 (로컬 전용) |

**의존성 추가 전에는 먼저 제안하고 확인을 받는다.**

---

## 8. 폴더 구조

```
job-match-agent/
├── CLAUDE.md               # 이 문서
├── README.md               # 구조도, 설계 이유, 평가 결과 (M6에서 작성)
├── .env.example            # LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
├── .gitignore              # .env, data/app.db, __pycache__ 등
├── requirements.txt
├── app.py                  # FastAPI (분석·결정·기록 API + 정적 파일 서빙)
├── web/                    # index.html, app.js, style.css
├── agent/
│   ├── llm.py              # 모델 호출 단일 진입점 (+ 429·5xx 재시도, 노드별 모델, 토큰/시간 로깅)
│   ├── llm_json.py         # 구조화 출력: JSON 응답 Pydantic 검증 + 1회 재시도
│   ├── llm_cache.py        # 개발·평가용 응답 디스크 캐시 (LLM_CACHE=1)
│   ├── schemas.py          # 4장 Pydantic 모델
│   ├── profile.py          # profile.yaml 로딩 + 경력(연차) 계산
│   ├── extract.py          # [extract]
│   ├── filters.py          # [filters] 순수 함수
│   ├── match.py            # [match] + 인용 존재 검증
│   ├── research.py         # [research] Tool Calling 루프
│   ├── tools.py            # web_search, fetch_page
│   ├── judge.py            # [judge] + [draft]
│   ├── graph.py            # 전체 흐름 (M3: 함수 호출 / M4: LangGraph)
│   └── store.py            # SQLite
├── config/profile.yaml     # 내 기준 (경력, 희망 방향, 지역 등) — 커밋 제외, profile.example.yaml 참고
├── data/resume.md          # 연락처 제거한 이력서·경력기술서 — 커밋 제외, resume.example.md 참고
├── eval/
│   ├── cases/              # 공고 원문: <case id>.txt / <case id>.png·jpg / <case id>/ 폴더(여러 장)
│   ├── labels.csv          # case id, 내 판단(지원/보류/스킵), 하드필터 기대값, 이유 — 커밋 제외, labels.example.csv 참고
│   └── run_eval.py         # 판단 일치율, 하드필터 정확도, 평균 시간·호출 수 출력
└── tests/
    └── test_filters.py
```

---

## 9. 단계별 진행 (한 단계씩, 완료 기준 확인 후 다음으로)

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **M0** | 저장소 뼈대, `.env.example`, `llm.py`에서 "안녕" 호출 성공 | `python -m agent.llm` 실행 시 응답 출력 |
| **M1** | `schemas.py` + `extract.py` (텍스트·이미지 입력 모두) | `eval/cases/` 공고 3개(이미지 1개 이상 포함)를 넣어 JobPosting JSON이 깨지지 않고 나옴. 경력 조건이 정확히 숫자로 추출됨 |
| **M2** | `filters.py` + `tests/test_filters.py` | pytest 통과. "경력 3년 이상 필수" 케이스가 FAIL |
| **M3** | `match.py`, `tools.py`, `research.py`(Tool Calling 직접 루프), `judge.py`, `graph.py`(순차 함수) | CLI에서 공고 파일 하나 넣으면 2장 예시 형태의 Markdown 리포트 출력 |
| **M4** | `graph.py`를 LangGraph로 이전. FAIL 시 조기 종료, match·research 병렬, 추천도에 따른 draft 분기 | 동일 입력에서 M3와 같은 리포트, 실행 시간 단축 로그 확인 |
| **M5** | `store.py` + `app.py`(FastAPI) + `web/` (텍스트·이미지 입력, 진행 단계 표시, 리포트, 결정 버튼, 기록 탭) | 브라우저에서 분석 → 결정 저장 → 기록 탭에 표시 |
| **M6** | `run_eval.py` + README | 라벨 대비 판단 일치율, 하드필터 정확도, 평균 응답 시간·LLM 호출 수를 README 표로 기록 |

각 단계가 끝나면: 무엇을 했는지 3줄 요약 + 다음 단계 제안. 사용자가 확인하면 다음으로 간다.

---

## 10. 작업 규칙

- **범위를 지킨다.** 요청받은 단계 외의 파일을 대규모로 바꾸지 않는다. 구조 변경이 필요하면 먼저 제안한다.
- **작게 만들고 바로 돌려본다.** 한 번에 여러 모듈을 쓰지 말고, 모듈 하나 → 실행 확인 → 다음.
- **비밀값:** API 키는 `.env`에만. 코드·로그·커밋에 절대 남기지 않는다.
- **개인정보:** `data/resume.md`에는 연락처가 없다. 새로 넣지 않는다. 무료 API는 입력이 서비스 개선에 쓰일 수 있음을 전제로 한다.
- **공개 저장소:** 이 저장소는 GitHub에 공개돼 있다. `data/resume.md`, `config/profile.yaml`, `eval/labels.csv`, `eval/cases/`의 공고 원문은 `.gitignore`로 제외하며 커밋하지 않는다. 커밋되는 파일(코드·문서·테스트·예시 파일)에는 실제 회사명과 개인 이력을 쓰지 않고 가상의 이름을 쓴다.
- **무료 한도:** LLM 호출 수를 노드별로 로깅한다. 분석 1건당 호출 수 목표 ≤ 8회. 429(한도 초과) 시 서버가 알려준 시간만큼 기다려 1회 재시도 후 사용자에게 안내(일일 한도 소진이면 재시도 없이 안내). 503(혼잡)은 지수 백오프로 최대 3회.
- **한도 실측:** Gemini 무료 등급은 모델별로 분당 5회·하루 20회였다(3.6 Flash, 2026-10). 실패한 요청도 횟수에 들어간다. 그래서 개발·테스트 중에는 `LLM_CACHE=1`을 켜고, 실제 호출이 필요한 확인은 최소한으로 한다. 노드별로 모델을 나눠(`LLM_MODEL_<NODE>`) 하루 사용량을 늘린다.
- **사실 생성 금지:** 회사 정보·이력서 근거는 출처/원문이 있는 것만. 없으면 "정보 없음".
- **한국어 출력.** 리포트·UI 문구는 한국어, 코드·변수명은 영어.
- 코드 스타일: 타입 힌트, 함수는 짧게, 노드별 모듈 분리.

---

## 11. 참고: 사용자 배경 (판단 맥락용)

- AI/LLM 엔지니어 지향, 경력 2년 남짓.
- 경력·강점·빈 곳은 `data/resume.md`, 판단 기준은 `config/profile.yaml`을 읽어 파악한다. 둘 다 개인 데이터라 저장소에는 없고 로컬에만 있다(형식은 `*.example` 파일 참고).
