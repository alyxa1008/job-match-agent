"use strict";

const STEPS = ["extract", "filters", "match", "research", "judge", "draft"];
const VERDICT_CLASS = { "지원 추천": "v-recommend", "지원 가능": "v-ok", "보류": "v-hold", "스킵 권장": "v-skip" };
const ALWAYS_SHOWN_RULES = new Set(["min_years", "employment_type", "location"]);
const MAX_IMAGES = 6;

const $ = (selector, root = document) => root.querySelector(selector);
const el = (tag, props = {}, children = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) node.append(child);
  return node;
};

// ---------- 탭 ----------
for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("is-active", p.id === `tab-${tab.dataset.tab}`));
    if (tab.dataset.tab === "history") loadHistory();
  });
}

// ---------- 입력 ----------
const files = [];
const dropzone = $("#dropzone");
const fileInput = $("#file-input");

const isImage = (file) => ["image/png", "image/jpeg"].includes(file.type) || /\.(png|jpe?g)$/i.test(file.name || "");

function addFiles(list) {
  const errorBox = $("#input-error");
  errorBox.hidden = true;
  const rejected = [];
  for (const file of list) {
    if (!isImage(file)) { rejected.push(file.name || "(이름 없음)"); continue; }
    if (files.length >= MAX_IMAGES) { rejected.push(`${file.name} (최대 ${MAX_IMAGES}장)`); continue; }
    files.push(file);
  }
  if (rejected.length) {
    errorBox.textContent = `넣지 못한 파일: ${rejected.join(", ")} — png·jpg만 받습니다.`;
    errorBox.hidden = false;
  }
  renderThumbs();
}

function droppedFiles(dataTransfer) {
  if (dataTransfer.files && dataTransfer.files.length) return [...dataTransfer.files];
  return [...dataTransfer.items].filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);
}

let draggingIndex = null; // 썸네일을 끌어 순서를 바꾸는 중이면 그 위치 (파일 끌어넣기와 구분)

function moveFile(from, to) {
  if (from === to) return;
  const [moved] = files.splice(from, 1);
  files.splice(to, 0, moved);
  renderThumbs();
}

function renderThumbs() {
  const ul = $("#thumbs");
  ul.replaceChildren(...files.map((file, index) => {
    const img = el("img", { src: URL.createObjectURL(file), alt: file.name });
    const order = el("span", { className: "order", textContent: String(index + 1) });
    const remove = el("button", { className: "remove", textContent: "×", title: "빼기" });
    remove.addEventListener("click", () => { files.splice(index, 1); renderThumbs(); });
    const li = el("li", { draggable: true, title: "끌어서 순서 바꾸기" }, [img, order, remove]);
    li.addEventListener("dragstart", (e) => {
      draggingIndex = index;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(index));
      li.classList.add("is-dragging");
    });
    li.addEventListener("dragend", () => { draggingIndex = null; renderThumbs(); });
    li.addEventListener("dragover", (e) => {
      if (draggingIndex === null) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      li.classList.add("is-target");
    });
    li.addEventListener("dragleave", () => li.classList.remove("is-target"));
    li.addEventListener("drop", (e) => {
      if (draggingIndex === null) return;
      e.preventDefault();
      e.stopPropagation();
      moveFile(draggingIndex, index);
      draggingIndex = null;
    });
    return li;
  }));
  const count = $("#thumb-count");
  count.textContent = files.length ? `캡처 ${files.length}장 — 번호 순서대로 한 공고로 읽습니다` : "";
}

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); });
fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
// 페이지 어디에 떨어뜨려도 받는다. 기본 동작(브라우저가 이미지 파일을 열어 버림)은 막는다.
document.addEventListener("dragover", (e) => {
  e.preventDefault();
  if (draggingIndex !== null) return; // 썸네일 순서 바꾸는 중
  e.dataTransfer.dropEffect = "copy";
  dropzone.classList.add("is-over");
});
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) dropzone.classList.remove("is-over"); });
document.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("is-over");
  if (draggingIndex !== null) return;
  addFiles(droppedFiles(e.dataTransfer));
});
document.addEventListener("paste", (e) => {
  const pasted = [...e.clipboardData.items].filter((i) => i.kind === "file").map((i) => i.getAsFile());
  if (pasted.length) { e.preventDefault(); addFiles(pasted); }
});
$("#clear-btn").addEventListener("click", () => { $("#posting-text").value = ""; files.length = 0; renderThumbs(); });

// ---------- 진행 단계 ----------
function resetSteps() {
  for (const li of document.querySelectorAll("#steps li")) {
    li.className = "";
    $(".time", li).textContent = "";
  }
  $("#run-summary").hidden = true;
}
function setStep(name, state, seconds) {
  const li = $(`#steps li[data-step="${name}"]`);
  if (!li) return;
  li.className = `is-${state}`;
  if (seconds !== undefined) $(".time", li).textContent = `${seconds.toFixed(1)}s`;
}

// ---------- 분석 ----------
$("#analyze-btn").addEventListener("click", analyze);

async function analyze() {
  const text = $("#posting-text").value;
  const errorBox = $("#input-error");
  errorBox.hidden = true;
  if (!text.trim() && files.length === 0) {
    errorBox.textContent = "공고 텍스트나 캡처를 넣어주세요.";
    errorBox.hidden = false;
    return;
  }
  const form = new FormData();
  form.append("text", text);
  files.forEach((file) => form.append("images", file));

  const button = $("#analyze-btn");
  button.disabled = true;
  resetSteps();
  setStep("extract", "running");
  $("#report").replaceChildren(el("p", { className: "placeholder", textContent: "분석 중입니다…" }));

  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    for await (const event of readLines(response.body)) handleEvent(event);
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
}

async function* readLines(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) if (line.trim()) yield JSON.parse(line);
  }
  if (buffer.trim()) yield JSON.parse(buffer);
}

function handleEvent(event) {
  if (event.event === "node") {
    setStep(event.name, "done", event.seconds);
    event.next.forEach((name) => setStep(name, "running"));
  } else if (event.event === "result") {
    for (const li of document.querySelectorAll("#steps li")) {
      if (!li.classList.contains("is-done")) li.className = "is-skipped";
    }
    const summary = $("#run-summary");
    summary.textContent = `${event.seconds.toFixed(1)}초 · LLM 호출 ${event.llm_calls}회`;
    summary.hidden = false;
    renderReport($("#report"), event.id, event.report, event.markdown, null);
  } else if (event.event === "error") {
    showError(event.message);
  }
}

function showError(message) {
  for (const li of document.querySelectorAll("#steps li.is-running")) li.className = "is-failed";
  $("#report").replaceChildren(el("p", { className: "error", textContent: `분석 실패: ${message}` }));
}

// ---------- 리포트 ----------
function renderReport(container, id, report, markdown, decision) {
  const node = $("#report-template").content.cloneNode(true);
  $(".report-title", node).textContent = `${report.posting.company} · ${report.posting.title}`;
  $(".stars", node).textContent = "★".repeat(report.score) + "☆".repeat(5 - report.score);
  const badge = $(".verdict-text", node);
  badge.textContent = report.verdict;
  badge.classList.add(VERDICT_CLASS[report.verdict] || "");

  const headline = { PASS: "✅ 통과", WARN: "⚠ 확인 필요", FAIL: "❌ 조건 미달" }[report.filters.overall];
  $(".filters h4", node).textContent = `하드 조건 · ${headline}`;
  fillList($(".filters ul", node), report.filters.items
    .filter((item) => item.status !== "PASS" || ALWAYS_SHOWN_RULES.has(item.rule))
    .map((item) => el("li", { className: `status-${item.status}`, textContent: item.message })));

  const failed = report.filters.overall === "FAIL";
  toggleSection($(".reasons", node), !failed, report.reasons.map((r) => el("li", { textContent: `· ${r}` })));
  const matched = report.match ? report.match.matched : [];
  toggleSection($(".matched", node), !failed && matched.length > 0, matched.map((m) =>
    el("li", {}, [`· ${m.requirement}`, el("span", { className: "quote", textContent: `"${m.resume_quote}"` })])));
  toggleSection($(".gaps", node), !failed && report.key_gaps.length > 0, report.key_gaps.map((g) => el("li", { textContent: `· ${g}` })));
  toggleSection($(".company", node), !failed && report.company !== null, companyLines(report.company));
  toggleSection($(".draft", node), Boolean(report.motivation_draft), []);
  if (report.motivation_draft) $(".draft p", node).textContent = report.motivation_draft;

  for (const button of node.querySelectorAll("[data-decision]")) {
    button.classList.toggle("is-selected", button.dataset.decision === decision);
    button.addEventListener("click", () => saveDecision(id, button.dataset.decision, button.parentElement));
  }
  $(".copy-md", node).addEventListener("click", () => navigator.clipboard.writeText(markdown));
  container.replaceChildren(node);
}

function companyLines(company) {
  if (!company) return [];
  if (company.error) return [el("li", { className: "warn-line", textContent: `⚠ 조사하지 못함 — ${company.error}` })];
  if (!company.found) return [el("li", { textContent: "· 정보 없음" })];
  const facts = company.facts.map((fact) => el("li", {}, [
    `· ${fact.text} `,
    el("span", { className: "source" }, ["(출처: ", el("a", { href: fact.source_url, target: "_blank", rel: "noopener", textContent: shortUrl(fact.source_url) }), ")"]),
  ]));
  const metrics = company.metrics.map((m) => el("li", {}, [
    `· ${METRIC_LABELS[m.name] || m.name} ${formatMetric(m)}${m.as_of ? ` (${m.as_of} 기준)` : ""} `,
    el("span", { className: "source" }, ["(출처: ", el("a", { href: m.source_url, target: "_blank", rel: "noopener", textContent: shortUrl(m.source_url) }), ")"]),
  ]));
  const warnings = company.warnings.map((w) => el("li", { className: "warn-line", textContent: `⚠ ${w}` }));
  return [...facts, ...metrics, ...warnings];
}

const METRIC_LABELS = { rating: "기업 리뷰 평점", review_count: "리뷰 수", headcount: "직원 수", joined_last_year: "최근 1년 입사", left_last_year: "최근 1년 퇴사" };
const METRIC_UNITS = { rating: "점", review_count: "개", headcount: "명", joined_last_year: "명", left_last_year: "명" };
const formatMetric = (m) => `${m.value}${METRIC_UNITS[m.name] || ""}`;

const shortUrl = (url) => { try { return new URL(url).hostname; } catch { return url; } };
function fillList(ul, items) { ul.replaceChildren(...items); }
function toggleSection(section, show, items) {
  section.hidden = !show;
  const ul = $("ul", section);
  if (ul) fillList(ul, items);
}

async function saveDecision(id, decision, group) {
  const response = await fetch(`/api/analyses/${id}/decision`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }),
  });
  if (!response.ok) return alert("결정을 저장하지 못했습니다.");
  for (const button of group.querySelectorAll("[data-decision]")) {
    button.classList.toggle("is-selected", button.dataset.decision === decision);
  }
}

// ---------- 기록 ----------
async function loadHistory() {
  const rows = await (await fetch("/api/analyses")).json();
  const tbody = $("#history-rows");
  $("#history-empty").hidden = rows.length > 0;
  tbody.replaceChildren(...rows.map((row) => {
    const tr = el("tr", {}, [
      el("td", { className: "muted", textContent: row.created_at.slice(0, 10) }),
      el("td", { textContent: `${row.company} · ${row.title}` }),
      el("td", { textContent: `${"★".repeat(row.score)} ${row.verdict}` }),
      el("td", { textContent: row.decision || "—" }),
    ]);
    tr.addEventListener("click", async () => {
      tbody.querySelectorAll("tr").forEach((r) => r.classList.toggle("is-selected", r === tr));
      const stored = await (await fetch(`/api/analyses/${row.id}`)).json();
      renderReport($("#history-report"), stored.id, stored.report, stored.report_md, stored.decision);
    });
    return tr;
  }));
}
