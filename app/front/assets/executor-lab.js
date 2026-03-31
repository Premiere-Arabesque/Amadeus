(function () {
  const elements = {
    message: document.getElementById("page-message"),
    loadDefaults: document.getElementById("load-defaults"),
    runLab: document.getElementById("run-lab"),
    stepTitle: document.getElementById("step-title"),
    stepDetail: document.getElementById("step-detail"),
    stepZone: document.getElementById("step-zone"),
    stepCapability: document.getElementById("step-capability"),
    stepArguments: document.getElementById("step-arguments"),
    relatedEventText: document.getElementById("related-event-text"),
    maxTurns: document.getElementById("max-turns"),
    bufferSeconds: document.getElementById("buffer-seconds"),
    interruptAfterTurn: document.getElementById("interrupt-after-turn"),
    nextStepScheduledFor: document.getElementById("next-step-scheduled-for"),
    roleplayName: document.getElementById("roleplay-name"),
    roleplaySoul: document.getElementById("roleplay-soul"),
    roleplayPlanContext: document.getElementById("roleplay-plan-context"),
    roleplayContextEntries: document.getElementById("roleplay-context-entries"),
    roleplayExtraInstructions: document.getElementById("roleplay-extra-instructions"),
    toolList: document.getElementById("tool-list"),
    resultSummary: document.getElementById("result-summary"),
    turnList: document.getElementById("turn-list"),
    toolInvocations: document.getElementById("tool-invocations"),
    executionTrace: document.getElementById("execution-trace"),
  };

  let cachedTools = [];

  function showMessage(text, kind = "info", timeout = 3600) {
    elements.message.textContent = text;
    elements.message.className = `page-message message-${kind}`;
    window.clearTimeout(showMessage._timer);
    showMessage._timer = window.setTimeout(() => {
      elements.message.className = "page-message hidden";
    }, timeout);
  }

  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Request failed: ${response.status}`);
    }
    return response.json();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatJson(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  function toIsoOrNull(value) {
    if (!value) {
      return null;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      throw new Error("下一个分钟级动作时间格式不正确。");
    }
    return date.toISOString();
  }

  function splitLines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function localizeZone(value) {
    const table = {
      auto: "自动",
      real: "Real Zone",
      non_real: "Non-Real Zone",
      "Real zone": "Real Zone",
      "Non-Real Zone": "Non-Real Zone",
    };
    return table[value] || value || "未知";
  }

  function renderTools(tools) {
    cachedTools = Array.isArray(tools) ? tools : [];
    elements.toolList.innerHTML = cachedTools.length
      ? cachedTools
          .map(
            (tool) => `
              <article class="tool-card">
                <h3>${escapeHtml(tool.name)}</h3>
                <p>${escapeHtml(tool.description || "无描述")}</p>
              </article>
            `
          )
          .join("")
      : `<div class="placeholder-card">当前没有可用工具。</div>`;

    elements.stepCapability.innerHTML = [
      `<option value="">自动解析</option>`,
      ...cachedTools.map(
        (tool) => `<option value="${escapeHtml(tool.name)}">${escapeHtml(tool.name)}</option>`
      ),
    ].join("");
  }

  function applyDefaults(payload) {
    if (!payload || !payload.roleplay) {
      return;
    }
    elements.stepTitle.value = payload.suggested_title || elements.stepTitle.value;
    elements.stepDetail.value = payload.suggested_detail || elements.stepDetail.value;
    elements.roleplayName.value = payload.roleplay.name || "Roleplay Agent";
    elements.roleplaySoul.value = payload.roleplay.soul_md || "";
    renderTools(payload.tools || []);
  }

  function buildPayload() {
    let argumentsPayload = {};
    try {
      argumentsPayload = JSON.parse(elements.stepArguments.value || "{}");
    } catch (error) {
      throw new Error(`arguments JSON 解析失败: ${error.message}`);
    }
    if (argumentsPayload === null || typeof argumentsPayload !== "object" || Array.isArray(argumentsPayload)) {
      throw new Error("arguments 必须是 JSON 对象。");
    }
    return {
      title: elements.stepTitle.value.trim(),
      detail: elements.stepDetail.value.trim(),
      zone: elements.stepZone.value,
      capability: elements.stepCapability.value.trim(),
      arguments: argumentsPayload,
      related_event_text: elements.relatedEventText.value.trim(),
      max_turns: Number(elements.maxTurns.value || "6"),
      buffer_seconds: Number(elements.bufferSeconds.value || "30"),
      interrupt_after_turn: elements.interruptAfterTurn.value
        ? Number(elements.interruptAfterTurn.value)
        : null,
      next_step_scheduled_for: toIsoOrNull(elements.nextStepScheduledFor.value),
      roleplay: {
        name: elements.roleplayName.value.trim() || "Roleplay Agent",
        soul_md: elements.roleplaySoul.value,
        plan_context: elements.roleplayPlanContext.value,
        context_entries: elements.roleplayContextEntries.value,
        extra_instructions: elements.roleplayExtraInstructions.value,
      },
    };
  }

  function renderSummary(payload) {
    elements.resultSummary.innerHTML = `
      <article class="summary-card">
        <h3>停止原因</h3>
        <p>${escapeHtml(payload.stop_reason || "未知")}</p>
        <p class="meta">resolved zone: ${escapeHtml(localizeZone(payload.resolved_zone))}</p>
      </article>
      <article class="summary-card">
        <h3>初始场景</h3>
        <p>${escapeHtml(payload.initial_scene || "")}</p>
      </article>
      <article class="summary-card">
        <h3>最终场景</h3>
        <p>${escapeHtml(payload.final_scene || "")}</p>
      </article>
      <article class="summary-card">
        <h3>最终结果</h3>
        <p>${escapeHtml(payload.final_result || "")}</p>
        <p class="meta">capability: ${escapeHtml(payload.resolved_capability || "无")}</p>
      </article>
    `;
  }

  function renderTurns(turns) {
    if (!Array.isArray(turns) || !turns.length) {
      elements.turnList.innerHTML = `<div class="placeholder-card">本轮没有留下中间轮次，通常表示一开始就自然停止了。</div>`;
      return;
    }
    elements.turnList.innerHTML = turns
      .map(
        (turn) => `
          <article class="turn-card">
            <div class="turn-meta">
              <span class="pill">${turn.turn_index === 0 ? "初始轮" : `第 ${escapeHtml(String(turn.turn_index))} 轮`}</span>
              <span class="pill zone">${escapeHtml(localizeZone(turn.zone))}</span>
              ${
                turn.next_zone
                  ? `<span class="pill stop">下一步 ${escapeHtml(localizeZone(turn.next_zone))}</span>`
                  : `<span class="pill stop">自然停止</span>`
              }
            </div>
            <h3>场景</h3>
            <p>${escapeHtml(turn.scene || "")}</p>
            <h3>结果</h3>
            <p>${escapeHtml(turn.result || "")}</p>
            <h3>Roleplay 回复</h3>
            <p>${escapeHtml(turn.roleplay_response || "")}</p>
            ${
              turn.next_scene
                ? `<h3>推进后的下一个场景</h3><p>${escapeHtml(turn.next_scene)}</p>`
                : ""
            }
          </article>
        `
      )
      .join("");
  }

  async function loadDefaults() {
    const payload = await request("/api/front/executor-lab/defaults");
    applyDefaults(payload);
    showMessage("已加载当前核心记忆和工具列表。", "success");
  }

  async function runLab() {
    const payload = buildPayload();
    const result = await request("/api/front/executor-lab/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderSummary(result);
    renderTurns(result.turns || []);
    elements.toolInvocations.textContent = formatJson(result.tool_invocations || []);
    elements.executionTrace.textContent = formatJson(result.execution_trace || []);
    showMessage("Executor Lab 已跑完这一轮。", "success");
  }

  async function safe(action) {
    try {
      elements.runLab.disabled = true;
      await action();
    } catch (error) {
      showMessage(error.message || "操作失败。", "error", 5200);
    } finally {
      elements.runLab.disabled = false;
    }
  }

  function bindEvents() {
    elements.loadDefaults.addEventListener("click", () => void safe(loadDefaults));
    elements.runLab.addEventListener("click", () => void safe(runLab));
  }

  async function init() {
    bindEvents();
    await safe(loadDefaults);
  }

  void init();
})();
