(function () {
  const state = {
    debug: null,
    activeSection: "context",
    activeTraceKind: "model",
    selectedTraceId: "",
    lastDecision: null,
  };

  const elements = {
    navLinks: Array.from(document.querySelectorAll(".nav-link")),
    sections: Array.from(document.querySelectorAll(".content-section")),
    traceTabs: Array.from(document.querySelectorAll(".trace-tab")),
    pageMessage: document.getElementById("page-message"),
    topMetrics: document.getElementById("top-metrics"),
    refreshDebug: document.getElementById("refresh-debug"),
    resetLab: document.getElementById("reset-lab"),
    personaName: document.getElementById("persona-name"),
    soulMd: document.getElementById("soul-md"),
    memoryLines: document.getElementById("memory-lines"),
    coreMemoryPreview: document.getElementById("core-memory-preview"),
    clockInput: document.getElementById("clock-input"),
    setClock: document.getElementById("set-clock"),
    expandReadyBlock: document.getElementById("expand-ready-block"),
    dayStart: document.getElementById("day-start"),
    dayStartNote: document.getElementById("day-start-note"),
    planSummary: document.getElementById("plan-summary"),
    dayBlocks: document.getElementById("day-blocks"),
    minuteSteps: document.getElementById("minute-steps"),
    decideOutcomeContent: document.getElementById("decide-outcome-content"),
    decideOutcomeStatus: document.getElementById("decide-outcome-status"),
    decideEventText: document.getElementById("decide-event-text"),
    decidePlanExhausted: document.getElementById("decide-plan-exhausted"),
    decideReplan: document.getElementById("decide-replan"),
    runReplanFlow: document.getElementById("run-replan-flow"),
    applyKind: document.getElementById("apply-kind"),
    applyReason: document.getElementById("apply-reason"),
    applyOutcomeContent: document.getElementById("apply-outcome-content"),
    applyReplan: document.getElementById("apply-replan"),
    replanDecisionCard: document.getElementById("replan-decision-card"),
    replanLogprobs: document.getElementById("replan-logprobs"),
    replanPlanPreview: document.getElementById("replan-plan-preview"),
    traceList: document.getElementById("trace-list"),
    traceDetailJson: document.getElementById("trace-detail-json"),
    traceRequest: document.getElementById("trace-request"),
    traceResponse: document.getElementById("trace-response"),
  };

  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Request failed: ${response.status}`);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  function formatJson(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function showMessage(text, kind = "info") {
    elements.pageMessage.textContent = text;
    elements.pageMessage.className = `page-message message-${kind}`;
    window.clearTimeout(showMessage.timerId);
    showMessage.timerId = window.setTimeout(() => {
      elements.pageMessage.className = "page-message hidden";
    }, 2600);
  }

  function currentContextPayload() {
    return {
      persona_name: (elements.personaName.value || "").trim() || "Amadeus",
      soul_md: elements.soulMd.value || "",
      memories: (elements.memoryLines.value || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    };
  }

  function currentDecidePayload() {
    return {
      ...currentContextPayload(),
      outcome_status: elements.decideOutcomeStatus.value,
      outcome_content: elements.decideOutcomeContent.value || "Manual replan decision.",
      event_text: elements.decideEventText.value || "",
      plan_exhausted: elements.decidePlanExhausted.checked,
    };
  }

  function formatDateTime(value) {
    if (!value) {
      return "N/A";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return date.toLocaleString("zh-CN", {
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function toDatetimeLocal(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const pad = (part) => String(part).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
      date.getHours()
    )}:${pad(date.getMinutes())}`;
  }

  function activateSection(sectionId) {
    state.activeSection = sectionId;
    elements.navLinks.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.section === sectionId);
    });
    elements.sections.forEach((section) => {
      section.classList.toggle("is-active", section.id === `section-${sectionId}`);
    });
  }

  function activateTraceKind(kind) {
    state.activeTraceKind = kind;
    state.selectedTraceId = "";
    elements.traceTabs.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.traceKind === kind);
    });
    renderTracePanel();
  }

  function renderTopMetrics(summary) {
    const metrics = [
      ["当前虚拟时间", formatDateTime(summary?.current_time)],
      ["运行状态", summary?.runtime_status || "unknown"],
      ["计划摘要", summary?.plan_summary || "尚未生成"],
      ["下一步时间", summary?.next_step_scheduled_for || "N/A"],
    ];
    elements.topMetrics.innerHTML = metrics
      .map(
        ([label, value]) => `
          <article class="metric-card">
            <p class="metric-label">${escapeHtml(label)}</p>
            <p class="metric-value">${escapeHtml(value)}</p>
          </article>
        `
      )
      .join("");
  }

  function renderCoreMemory(debug) {
    elements.coreMemoryPreview.textContent = formatJson(debug?.core_memory || {});
    if (!elements.clockInput.value && debug?.summary?.current_time) {
      elements.clockInput.value = toDatetimeLocal(debug.summary.current_time);
    }
    if (!elements.personaName.value && debug?.summary?.persona_name) {
      elements.personaName.value = debug.summary.persona_name;
    }
    if (!elements.soulMd.value && debug?.core_memory?.soul_md) {
      elements.soulMd.value = debug.core_memory.soul_md;
    }
    if (!elements.memoryLines.value && Array.isArray(debug?.core_memory?.recent_events)) {
      elements.memoryLines.value = debug.core_memory.recent_events.join("\n");
    }
  }

  function renderPlanBlocks(plan) {
    const dayBlocks = Array.isArray(plan?.day_blocks) ? plan.day_blocks : [];
    if (!dayBlocks.length) {
      elements.dayBlocks.innerHTML = `<div class="empty-state">还没有生成时间块计划。</div>`;
      return;
    }
    elements.dayBlocks.innerHTML = dayBlocks
      .map((block) => {
        const classes = ["plan-item", block.status || ""].join(" ").trim();
        return `
          <button
            class="${classes} plan-item-button"
            data-block-id="${escapeHtml(block.block_id || "")}"
            type="button"
          >
            <h5 class="plan-item-title">${escapeHtml(block.time || "")}</h5>
            <p class="plan-item-body">${escapeHtml(block.label || "")}</p>
            <div class="plan-meta">
              <span>${escapeHtml(block.status || "")}</span>
              <span class="mono-inline">${escapeHtml(block.block_id || "")}</span>
            </div>
          </button>
        `;
      })
      .join("");
    elements.dayBlocks.querySelectorAll("[data-block-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await expandSpecificBlock(button.dataset.blockId || "");
        } catch (error) {
          showMessage(error.message || String(error), "error");
        }
      });
    });
  }

  function renderMinuteSteps(plan, target) {
    const minuteSteps = Array.isArray(plan?.minute_steps) ? plan.minute_steps : [];
    if (!minuteSteps.length) {
      target.innerHTML = `<div class="empty-state">当前没有展开的分钟动作。</div>`;
      return;
    }
    target.innerHTML = minuteSteps
      .map(
        (step) => `
          <article class="plan-item ${escapeHtml(step.status || "")}">
            <h5 class="plan-item-title">${escapeHtml(step.title || "")}</h5>
            <p class="plan-item-body">${escapeHtml(step.detail || "")}</p>
            <div class="plan-meta">
              <span>${escapeHtml(step.scheduled_for || "N/A")}</span>
              <span>${escapeHtml(String(step.minutes || ""))} min</span>
              <span>${escapeHtml(step.status || "")}</span>
            </div>
          </article>
        `
      )
      .join("");
  }

  function renderPlanSummary(debug) {
    const plan = debug?.current_plan || {};
    const summary = debug?.summary || {};
    elements.planSummary.innerHTML = `
      <span>plan_date: ${escapeHtml(plan.plan_date || "N/A")}</span>
      <span>active_block_id: ${escapeHtml(plan.active_block_id || "N/A")}</span>
      <span>current_time: ${escapeHtml(summary.current_time || "N/A")}</span>
      <span>runtime_status: ${escapeHtml(summary.runtime_status || "N/A")}</span>
    `;
    renderPlanBlocks(plan);
    renderMinuteSteps(plan, elements.minuteSteps);
  }

  function latestDecisionFromDebug(debug) {
    if (state.lastDecision) {
      return state.lastDecision;
    }
    const replanEntries = debug?.replan_entries || [];
    return replanEntries[0]?.payload?.decision || null;
  }

  function findLatestReplanLogprobEntry(debug) {
    const modelEntries = debug?.model_entries || [];
    return (
      modelEntries.find(
        (entry) => entry?.payload?.model_settings?.openai_logprobs === true
      ) || null
    );
  }

  function renderReplanSection(debug) {
    const decision = latestDecisionFromDebug(debug);
    if (!decision) {
      elements.replanDecisionCard.className = "detail-card empty-state";
      elements.replanDecisionCard.textContent = "还没有执行过 replan decide。";
    } else {
      elements.replanDecisionCard.className = "detail-card";
      elements.replanDecisionCard.innerHTML = `
        <div class="plan-meta">
          <span>kind: ${escapeHtml(decision.kind || "N/A")}</span>
          <span>source: ${escapeHtml(decision.source || "N/A")}</span>
          <span>confidence: ${escapeHtml(
            typeof decision.confidence === "number"
              ? decision.confidence.toFixed(6)
              : "N/A"
          )}</span>
        </div>
        <p class="plan-item-body">${escapeHtml(decision.reason || "没有附带 reason。")}</p>
      `;
    }

    const logprobEntry = findLatestReplanLogprobEntry(debug);
    const logprobs = logprobEntry?.payload?.provider_details?.logprobs;
    if (!Array.isArray(logprobs) || !logprobs.length) {
      elements.replanLogprobs.textContent = "{}";
    } else {
      const first = logprobs[0] || {};
      elements.replanLogprobs.textContent = formatJson({
        selected_token: first.token,
        selected_logprob: first.logprob,
        top_logprobs: first.top_logprobs || [],
      });
    }

    const preview = debug?.current_plan || {};
    elements.replanPlanPreview.innerHTML = `
      <div class="summary-strip">
        <span>day_summary: ${escapeHtml(preview.day_summary || "N/A")}</span>
        <span>active_block_id: ${escapeHtml(preview.active_block_id || "N/A")}</span>
      </div>
      <div class="plan-layout">
        <div>
          <h5>时间块</h5>
          <div class="plan-list">${elements.dayBlocks.innerHTML}</div>
        </div>
        <div>
          <h5>分钟动作</h5>
          <div class="plan-list">${elements.minuteSteps.innerHTML}</div>
        </div>
      </div>
    `;
  }

  function tracesByKind(debug, kind) {
    if (kind === "planning") {
      return debug?.planning_entries || [];
    }
    if (kind === "replan") {
      return debug?.replan_entries || [];
    }
    return debug?.model_entries || [];
  }

  function traceSummary(entry, kind) {
    const payload = entry?.payload || {};
    if (kind === "planning") {
      return {
        title: payload.plan_scope || "planning",
        meta: `${payload.strategy || ""} · ${formatDateTime(entry.created_at)}`,
      };
    }
    if (kind === "replan") {
      const decision = payload.decision || {};
      return {
        title: decision.kind || "replan",
        meta: `${decision.source || ""} · ${formatDateTime(entry.created_at)}`,
      };
    }
    return {
      title: `${payload.role || "model"} / ${payload.request_kind || ""}`,
      meta: `${payload.model || ""} · ${formatDateTime(entry.created_at)}`,
    };
  }

  function renderTracePanel() {
    const entries = tracesByKind(state.debug, state.activeTraceKind);
    if (!entries.length) {
      elements.traceList.innerHTML = `<div class="empty-state">当前没有可用 trace。</div>`;
      elements.traceDetailJson.textContent = "{}";
      elements.traceRequest.textContent = "";
      elements.traceResponse.textContent = "";
      return;
    }
    if (!state.selectedTraceId || !entries.some((entry) => entry.entry_id === state.selectedTraceId)) {
      state.selectedTraceId = entries[0].entry_id;
    }
    elements.traceList.innerHTML = entries
      .map((entry) => {
        const summary = traceSummary(entry, state.activeTraceKind);
        const active = entry.entry_id === state.selectedTraceId;
        return `
          <button class="trace-item ${active ? "is-active" : ""}" data-trace-id="${escapeHtml(
            entry.entry_id
          )}" type="button">
            <strong>${escapeHtml(summary.title)}</strong>
            <div class="trace-meta"><span>${escapeHtml(summary.meta)}</span></div>
          </button>
        `;
      })
      .join("");
    elements.traceList.querySelectorAll("[data-trace-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedTraceId = button.dataset.traceId || "";
        renderTracePanel();
      });
    });

    const selected = entries.find((entry) => entry.entry_id === state.selectedTraceId) || entries[0];
    const payload = selected?.payload || {};
    elements.traceDetailJson.textContent = formatJson(payload);

    if (state.activeTraceKind === "model") {
      const exchanges = payload.http_exchanges || [];
      const latest = exchanges[exchanges.length - 1] || {};
      elements.traceRequest.textContent = latest?.request?.body || "";
      elements.traceResponse.textContent = latest?.response?.body || "";
    } else {
      elements.traceRequest.textContent = "";
      elements.traceResponse.textContent = "";
    }
  }

  function renderAll(debug) {
    state.debug = debug;
    renderTopMetrics(debug?.summary || {});
    renderCoreMemory(debug);
    renderPlanSummary(debug);
    renderReplanSection(debug);
    renderTracePanel();
  }

  async function refreshDebug() {
    const payload = await request("/api/planner-lab/debug?limit=24");
    renderAll(payload);
  }

  async function setClock() {
    if (!elements.clockInput.value) {
      throw new Error("请先填写当前虚拟时间。");
    }
    const payload = await request("/api/planner-lab/clock/set", {
      method: "POST",
      body: JSON.stringify({
        at: new Date(elements.clockInput.value).toISOString(),
      }),
    });
    state.lastDecision = null;
    renderAll(payload.debug);
    showMessage("虚拟时间已更新。", "success");
  }

  async function dayStart() {
    const payload = await request("/api/planner-lab/day-start", {
      method: "POST",
      body: JSON.stringify({
        ...currentContextPayload(),
        note: elements.dayStartNote.value || "",
      }),
    });
    state.lastDecision = null;
    renderAll(payload.debug);
    activateSection("planning");
    showMessage("已重新生成计划表。", "success");
  }

  async function expandReadyBlock() {
    const payload = await request("/api/planner-lab/expand-ready-block", {
      method: "POST",
      body: JSON.stringify({
        ...currentContextPayload(),
        reason: "Manual expand from planner workbench.",
        force: true,
      }),
    });
    state.lastDecision = null;
    renderAll(payload.debug);
    activateSection("planning");
    showMessage("已按当前时间展开时段。", "success");
  }

  async function expandSpecificBlock(blockId) {
    if (!blockId) {
      throw new Error("缺少 block_id，无法展开指定时间段。");
    }
    const payload = await request("/api/planner-lab/expand-block", {
      method: "POST",
      body: JSON.stringify({
        ...currentContextPayload(),
        block_id: blockId,
        reason: "Manual block expansion from planner workbench.",
      }),
    });
    state.lastDecision = null;
    renderAll(payload.debug);
    activateSection("planning");
    showMessage("已展开所选时间段。", "success");
  }

  async function decideReplan() {
    const payload = await request("/api/planner-lab/replan/decide", {
      method: "POST",
      body: JSON.stringify(currentDecidePayload()),
    });
    state.lastDecision = payload.decision;
    renderAll(payload.debug);
    activateSection("replan");
    showMessage("Replan decide 已完成。", "success");
  }

  async function applyReplan() {
    const payload = await request("/api/planner-lab/replan/apply", {
      method: "POST",
      body: JSON.stringify({
        ...currentContextPayload(),
        kind: elements.applyKind.value,
        reason: elements.applyReason.value || "",
        outcome_content: elements.applyOutcomeContent.value || "Manual replan apply.",
      }),
    });
    state.lastDecision = null;
    renderAll(payload.debug);
    activateSection("replan");
    showMessage("Replan apply 已完成。", "success");
  }

  async function runReplanFlow() {
    const decisionPayload = await request("/api/planner-lab/replan/decide", {
      method: "POST",
      body: JSON.stringify(currentDecidePayload()),
    });
    state.lastDecision = decisionPayload.decision;

    const decision = decisionPayload.decision || {};
    elements.applyKind.value =
      decision.kind === "hour_replan" ? "hour_replan" : "micro_replan";
    elements.applyReason.value = decision.reason || "";
    elements.applyOutcomeContent.value =
      elements.decideOutcomeContent.value || "Manual replan apply.";

    if (decision.kind === "no_replan") {
      renderAll(decisionPayload.debug);
      activateSection("replan");
      showMessage("判定结果为 no_replan，当前计划表保持不变。", "info");
      return;
    }

    const applyPayload = await request("/api/planner-lab/replan/apply", {
      method: "POST",
      body: JSON.stringify({
        ...currentContextPayload(),
        kind: decision.kind,
        reason: decision.reason || "",
        outcome_content: elements.decideOutcomeContent.value || "Manual replan apply.",
      }),
    });
    renderAll(applyPayload.debug);
    activateSection("replan");
    showMessage(`已完成 ${decision.kind} 并更新当前计划表。`, "success");
  }

  async function resetLab() {
    const payload = await request("/api/planner-lab/reset", { method: "POST" });
    state.lastDecision = null;
    renderAll(payload.debug);
    showMessage("实验台已重置。", "success");
  }

  function bindEvents() {
    elements.navLinks.forEach((button) => {
      button.addEventListener("click", () => activateSection(button.dataset.section || "context"));
    });
    elements.traceTabs.forEach((button) => {
      button.addEventListener("click", () => activateTraceKind(button.dataset.traceKind || "model"));
    });
    elements.refreshDebug.addEventListener("click", async () => {
      try {
        await refreshDebug();
        showMessage("状态已刷新。", "info");
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.resetLab.addEventListener("click", async () => {
      try {
        await resetLab();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.setClock.addEventListener("click", async () => {
      try {
        await setClock();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.dayStart.addEventListener("click", async () => {
      try {
        await dayStart();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.expandReadyBlock.addEventListener("click", async () => {
      try {
        await expandReadyBlock();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.decideReplan.addEventListener("click", async () => {
      try {
        await decideReplan();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.runReplanFlow.addEventListener("click", async () => {
      try {
        await runReplanFlow();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
    elements.applyReplan.addEventListener("click", async () => {
      try {
        await applyReplan();
      } catch (error) {
        showMessage(error.message || String(error), "error");
      }
    });
  }

  async function bootstrap() {
    bindEvents();
    try {
      await refreshDebug();
    } catch (error) {
      showMessage(error.message || String(error), "error");
    }
  }

  bootstrap();
})();
