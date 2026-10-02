"""[report] Report → 사람이 읽는 Markdown 텍스트."""

from __future__ import annotations

from agent.schemas import CompanyInfo, HardFilterResult, Report

MAX_SCORE = 5
ALWAYS_SHOWN_RULES = {"min_years", "employment_type", "location"}  # PASS여도 보여주는 조건
FILTER_HEADLINES = {"PASS": "✅ 통과", "WARN": "⚠ 확인 필요", "FAIL": "❌ 조건 미달"}
STATUS_MARKS = {"PASS": "·", "WARN": "⚠", "FAIL": "❌"}


def _stars(score: int) -> str:
    return "★" * score + "☆" * (MAX_SCORE - score)


def _filter_lines(filters: HardFilterResult) -> list[str]:
    lines = [f"[하드 조건] {FILTER_HEADLINES[filters.overall]}"]
    for item in filters.items:
        if item.status != "PASS" or item.rule in ALWAYS_SHOWN_RULES:
            lines.append(f" {STATUS_MARKS[item.status]} {item.message}")
    return lines


def _company_lines(company: CompanyInfo) -> list[str]:
    if company.error:
        return [f" · ⚠ 조사하지 못함 — {company.error}"]
    lines = [f" · {fact.text}  (출처: {fact.source_url})" for fact in company.facts]
    lines += [f" · ⚠ {warning}" for warning in company.warnings]
    return lines or [" · 정보 없음"]


def _section(title: str, lines: list[str]) -> list[str]:
    return ["", f"[{title}]", *lines] if lines else []


def render_report(report: Report) -> str:
    lines = [
        f"{report.posting.company} · {report.posting.title}",
        f"추천도 {_stars(report.score)} → {report.verdict}",
        "",
        *_filter_lines(report.filters),
    ]
    if report.filters.overall == "FAIL":
        return "\n".join(lines)

    lines += _section("판단 이유", [f" · {reason}" for reason in report.reasons])
    if report.match:
        evidence_lines = [
            line
            for evidence in report.match.matched
            for line in (f" · {evidence.requirement}", f'   근거: "{evidence.resume_quote}"')
        ]
        lines += _section("맞는 점", evidence_lines)
    lines += _section("빈 점", [f" · {gap}" for gap in report.key_gaps])
    if report.company:
        lines += _section("회사", _company_lines(report.company))
    if report.motivation_draft:
        lines += _section("지원동기 초안", [report.motivation_draft])
    return "\n".join(lines)
