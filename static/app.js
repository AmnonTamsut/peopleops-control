/* All figures come from the Python service. Source text is rendered with textContent. */
"use strict";
const el = (id) => document.getElementById(id);
const state = { dashboard: null, severity: "all", page: 1, request: 0, busy: false };
const PAGE_SIZE = 10;
const integer = new Intl.NumberFormat("en-IL", { maximumFractionDigits: 0 });
const currency = new Intl.NumberFormat("en-IL", { style: "currency", currency: "ILS", maximumFractionDigits: 2, minimumFractionDigits: 2 });
const money = (cents) => currency.format(Number(cents ?? 0) / 100);
const compactMoney = (cents) => new Intl.NumberFormat("en-IL", { style: "currency", currency: "ILS", maximumFractionDigits: 0 }).format(Number(cents ?? 0) / 100);
const words = (value) => String(value ?? "Unknown").replace(/[_-]/g, " ");
const node = (tag, className, content) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (content !== undefined) result.textContent = content;
  return result;
};
const statusClass = (status) => {
  const value = String(status ?? "").toLowerCase();
  if (["blocked", "critical", "invalid", "quarantined", "failed"].includes(value)) return "critical";
  if (["warning", "review", "reviewed", "variance", "mismatch"].includes(value)) return "warning";
  if (["published", "clean", "passed", "ok", "matched", "success", "ready", "accepted", "validated"].includes(value)) return "published";
  return "neutral";
};
const pill = (status) => node("span", `status-pill ${statusClass(status)}`, words(status));
const shortId = (value) => String(value ?? "—").slice(0, 12);
const time = (value) => {
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("en-GB", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
};
function feedback(message, isError = false) {
  el("feedback").textContent = message;
  el("feedback").className = `feedback${isError ? " error" : ""}`;
  el("feedback").hidden = !message;
}
async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "";
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : ""; } catch (_) { /* Non-JSON server error. */ }
    throw new Error(detail || `The service returned HTTP ${response.status}. Please try again.`);
  }
  return response.json();
}
function reportHref(id) { return `/api/runs/${encodeURIComponent(id)}/report`; }
function showNoRun() {
  el("run-state").textContent = "Not run";
  el("run-state").className = "status-pill neutral";
  el("run-description").textContent = "No pipeline run for this period. Choose a scenario to create one.";
  el("report-link").hidden = true;
  el("metric-context").textContent = "No run available for this period";
  for (const id of ["headcount", "gross", "variance", "quality"]) el(id).textContent = "—";
  el("headcount-note").textContent = "Run the pipeline to reconcile records";
  el("quality-note").textContent = "Source validation and reconciliation";
}
function render(data) {
  state.dashboard = data;
  state.page = 1;
  const run = data.run;
  const metrics = data.metrics ?? {};
  if (run) {
    const blocked = Number(run.critical_count) > 0 || String(run.status).toLowerCase() === "blocked";
    el("run-state").textContent = words(run.status);
    el("run-state").className = `status-pill ${blocked ? "critical" : statusClass(run.status)}`;
    el("run-description").textContent = `${integer.format(run.accepted_rows ?? 0)} accepted · ${integer.format(run.rejected_rows ?? 0)} quarantined · ${integer.format(run.duration_ms ?? 0)} ms${run.reused ? " · Existing run reused" : ""}`;
    el("report-link").href = reportHref(run.id);
    el("report-link").hidden = false;
    el("metric-context").textContent = blocked
      ? `Attempted run metrics · Publication blocked${metrics.published_run_id ? ` · Last good publication: ${shortId(metrics.published_run_id)}` : " · No published run yet"}`
      : `Selected run metrics · ${metrics.published_run_id === run.id ? "Published for this period" : "Publication: " + (metrics.published_run_id ? shortId(metrics.published_run_id) : "none")}`;
    el("headcount").textContent = integer.format(metrics.headcount ?? 0);
    el("headcount-note").textContent = `${integer.format(run.input_rows ?? 0)} source rows processed`;
    el("gross").textContent = compactMoney(metrics.gross_cents);
    el("gross").title = money(metrics.gross_cents);
    el("variance").textContent = money(metrics.variance_cents);
    el("quality").textContent = `${Number(metrics.quality_score ?? 0).toFixed(1).replace(/\.0$/, "")}%`;
    el("quality-note").textContent = `${integer.format(run.critical_count ?? 0)} critical · ${integer.format(run.warning_count ?? 0)} warnings`;
  } else showNoRun();
  renderDepartments(data.departments ?? []);
  renderSources(data.sources ?? []);
  const selectedRule = el("rule-filter").value;
  const options = [node("option", "", "All rules")];
  options[0].value = "all";
  [...new Set((data.findings ?? []).map((finding) => finding.rule))].sort().forEach((rule) => {
    const option = node("option", "", words(rule)); option.value = rule; options.push(option);
  });
  el("rule-filter").replaceChildren(...options);
  if (options.some((option) => option.value === selectedRule)) el("rule-filter").value = selectedRule;
  renderFindings(); renderRecords(); renderHistory(data.runs ?? []);
}
function renderDepartments(departments) {
  el("department-total").textContent = `${departments.length} departments`;
  if (!departments.length) { el("department-chart").replaceChildren(node("p", "empty-state", "Run the pipeline to see department totals.")); return; }
  const max = Math.max(...departments.map((department) => Math.abs(Number(department.gross_cents ?? 0))), 1);
  const rows = [...departments].sort((a, b) => b.gross_cents - a.gross_cents).map((department) => {
    const row = node("div", "department-row");
    const name = node("span", "department-name", department.name);
    name.title = `${department.name} · ${department.headcount} employees`;
    const track = node("div", "bar-track"); track.setAttribute("aria-hidden", "true");
    const bar = node("div", "bar-fill"); bar.style.width = `${Math.min(100, Math.max(0, Math.abs(Number(department.gross_cents ?? 0)) / max * 100))}%`;
    track.append(bar);
    const amount = node("span", "department-amount", compactMoney(department.gross_cents)); amount.title = money(department.gross_cents);
    row.append(name, track, amount); return row;
  });
  el("department-chart").replaceChildren(...rows);
}
function renderSources(sources) {
  const rows = sources.map((source) => {
    const row = node("div", "source-row");
    const icon = node("span", "source-icon", String(source.type ?? "DATA").toUpperCase().slice(0, 4));
    const copy = node("div", "source-copy");
    copy.append(node("strong", "", source.name), node("small", "", `${words(source.type)} · ${integer.format(source.rows ?? 0)} source rows`));
    row.append(icon, copy, pill(source.status));
    return row;
  });
  el("sources").replaceChildren(...(rows.length ? rows : [node("p", "empty-state", "No source evidence for this period yet.")]));
}
function renderFindings() {
  const all = state.dashboard?.findings ?? [];
  el("findings-count").textContent = integer.format(all.length);
  const visible = all.filter((finding) => (state.severity === "all" || finding.severity === state.severity) && (el("rule-filter").value === "all" || finding.rule === el("rule-filter").value));
  if (!visible.length) {
    const empty = node("div", all.length ? "empty-state" : "empty-good");
    if (all.length) empty.textContent = "No findings match these filters.";
    else if (!state.dashboard?.run) empty.textContent = "Quality findings will appear after a pipeline run.";
    else empty.append(node("strong", "", "All checks passed"), node("span", "", "No exceptions in this run. The evidence is ready to inspect."));
    el("findings").replaceChildren(empty); return;
  }
  const rows = visible.map((finding) => {
    const article = node("article", "finding");
    const marker = node("span", `finding-marker ${statusClass(finding.severity)}`); marker.setAttribute("aria-hidden", "true");
    const body = node("div", "finding-body");
    body.append(node("h3", "", words(finding.rule)));
    const meta = node("div", "finding-meta");
    [finding.employee_id ?? "Source-level finding", finding.employee_name, finding.department].filter(Boolean).forEach((text) => meta.append(node("span", "", text)));
    body.append(meta, node("p", "finding-message", finding.message));
    const remediation = node("p", "finding-remediation");
    remediation.append(node("strong", "", "Next step: "), document.createTextNode(String(finding.remediation ?? "Inspect the source evidence.")));
    body.append(remediation);
    const owner = node("div", "finding-owner");
    owner.append(pill(finding.severity), node("small", "", `Owner: ${finding.owner ?? "Unassigned"}`));
    if (Number(finding.impact_cents)) owner.append(node("small", "", `Impact: ${money(finding.impact_cents)}`));
    article.append(marker, body, owner); return article;
  });
  el("findings").replaceChildren(...rows);
}
function renderRecords() {
  const query = el("employee-search").value.trim().toLowerCase();
  const all = state.dashboard?.records ?? [];
  const filtered = all.filter((record) => [record.employee_id, record.employee_name, record.department].some((value) => String(value ?? "").toLowerCase().includes(query)));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.page = Math.max(1, Math.min(state.page, pages));
  const start = (state.page - 1) * PAGE_SIZE;
  const rows = filtered.slice(start, start + PAGE_SIZE).map((record) => {
    const row = node("tr");
    const employee = node("td"); employee.append(node("strong", "", record.employee_name ?? record.employee_id), node("small", "", record.employee_id));
    const variance = node("td", `number${Number(record.variance_cents) ? " variance-cell" : ""}`, money(record.variance_cents));
    const status = node("td"); status.append(pill(record.status));
    row.append(employee, node("td", "", record.department), node("td", "number", money(record.expected_cents)), node("td", "number", money(record.gross_cents)), variance, status); return row;
  });
  if (!rows.length) { const row = node("tr"); const cell = node("td", "empty-state", query ? "No employees match your search." : "No reconciliation records for this period yet."); cell.colSpan = 6; row.append(cell); rows.push(row); }
  el("records").replaceChildren(...rows);
  el("records-count").textContent = filtered.length ? `Showing ${start + 1}–${Math.min(start + PAGE_SIZE, filtered.length)} of ${filtered.length} employees${query ? ` · ${all.length} total` : ""}` : "0 employees";
  el("page-number").textContent = `${state.page} / ${pages}`;
  el("previous-page").disabled = state.page <= 1;
  el("next-page").disabled = state.page >= pages;
}
function renderHistory(runs) {
  const rows = runs.slice(0, 5).map((run) => {
    const row = node("div", "history-row");
    const icon = node("span", "history-icon", Number(run.critical_count) ? "!" : "✓"); icon.setAttribute("aria-hidden", "true");
    const copy = node("div", "history-copy");
    copy.append(node("strong", "", `${run.period} · ${words(run.scenario)}`), node("small", "", `${time(run.started_at)} · ${shortId(run.id)}`));
    const link = node("a", "text-link", "Excel ↗"); link.href = reportHref(run.id); link.setAttribute("aria-label", `Download Excel evidence for ${run.period} ${run.scenario} run ${shortId(run.id)}`);
    row.append(icon, copy, pill(run.status), link); return row;
  });
  el("run-history").replaceChildren(...(rows.length ? rows : [node("p", "empty-state", "Run the pipeline to start an audit trail.")]));
}
async function loadDashboard() {
  const version = ++state.request;
  const period = el("period").value;
  el("main").setAttribute("aria-busy", "true");
  try {
    const data = await request(`/api/dashboard?period=${encodeURIComponent(period)}`);
    if (version === state.request) render(data);
    return true;
  } catch (error) {
    if (version === state.request) {
      feedback(`Could not load this period. ${error.message}`, true);
      el("run-state").textContent = "Unavailable";
      el("run-state").className = "status-pill critical";
      el("run-description").textContent = "The selected period could not be loaded. Choose the period again to retry.";
    }
    return false;
  } finally {
    if (version === state.request) el("main").setAttribute("aria-busy", "false");
  }
}
async function runPipeline() {
  if (state.busy) return;
  state.busy = true;
  const period = el("period").value;
  const scenario = el("scenario").value;
  const button = el("run-button");
  button.disabled = true;
  button.textContent = "Running pipeline…";
  el("period").disabled = true; el("scenario").disabled = true;
  feedback("Extracting sources, validating records, and preparing reconciliation evidence…");
  try {
    const result = await request("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ period, scenario }) });
    const loaded = await loadDashboard();
    if (!loaded) return;
    // A reused run can predate the latest-created run. Show the evidence the
    // user actually requested, while preserving the refreshed audit history.
    render({ ...result, runs: state.dashboard?.runs ?? [] });
    const run = result.run ?? result;
    const blocked = Number(run.critical_count) > 0 || String(run.status).toLowerCase() === "blocked";
    feedback(run.reused ? "Identical inputs detected. The existing immutable run and its evidence were reused." : blocked ? "Reconciliation completed with critical findings. Publication is blocked; review the exceptions below. Any prior published run remains available." : "Reconciliation completed. Review the checks and download the Excel evidence pack.");
  } catch (error) { feedback(`The pipeline could not complete. ${error.message}`, true); }
  finally {
    state.busy = false;
    button.disabled = false;
    button.replaceChildren(node("span", "", "▶"), document.createTextNode(" Run pipeline"));
    button.firstChild.setAttribute("aria-hidden", "true");
    el("period").disabled = false; el("scenario").disabled = false;
  }
}
el("period").addEventListener("change", () => {
  feedback("");
  render({ run: null, metrics: {}, departments: [], sources: [], records: [], findings: [], runs: [] });
  el("run-state").textContent = "Loading";
  el("run-description").textContent = "Loading reconciliation evidence for the selected period…";
  loadDashboard();
});
el("run-button").addEventListener("click", runPipeline);
el("rule-filter").addEventListener("change", renderFindings);
el("employee-search").addEventListener("input", () => { state.page = 1; renderRecords(); });
el("previous-page").addEventListener("click", () => { state.page -= 1; renderRecords(); });
el("next-page").addEventListener("click", () => { state.page += 1; renderRecords(); });
document.querySelectorAll("[data-severity]").forEach((button) => {
  button.addEventListener("click", () => {
    state.severity = button.dataset.severity;
    document.querySelectorAll("[data-severity]").forEach((other) => {
      const selected = other === button; other.classList.toggle("selected", selected); other.setAttribute("aria-pressed", String(selected));
    });
    renderFindings();
  });
});
document.querySelectorAll(".nav-item").forEach((link) => link.addEventListener("click", () => {
  document.querySelectorAll(".nav-item").forEach((other) => other.classList.toggle("active", other === link));
}));
loadDashboard();
