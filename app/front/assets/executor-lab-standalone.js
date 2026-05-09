(function () {
  const elements = {
    message: document.getElementById("page-message"),
    loadDefaults: document.getElementById("load-defaults"),
    runLab: document.getElementById("run-lab"),
    stopLab: document.getElementById("stop-lab"),
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
    roleplayRegisteredContacts: document.getElementById("roleplay-registered-contacts"),
    toolList: document.getElementById("tool-list"),
    streamLog: document.getElementById("stream-log"),
    resultSummary: document.getElementById("result-summary"),
    turnList: document.getElementById("turn-list"),
    toolInvocations: document.getElementById("tool-invocations"),
    executionTrace: document.getElementById("execution-trace"),
  };

  let cachedTools = [];
  let streamTurns = [];
  let completed = false;
  let currentController = null;
  let isStreaming = false;
  const FORM_STORAGE_KEY = "amadeus.executor-lab.form.v1";

  function showMessage(text, kind = "info", sticky = false) {
    elements.message.textContent = text;
    elements.message.className = `page-message message-${kind}`;
    window.clearTimeout(showMessage._timer);
    if (!sticky) {
      showMessage._timer = window.setTimeout(() => {
        elements.message.className = "page-message hidden";
      }, 4200);
    }
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

  function readStoredFormState() {
    try {
      const raw = window.localStorage.getItem(FORM_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (error) {
      return null;
    }
  }

  function collectFormState() {
    return {
      stepTitle: elements.stepTitle.value,
      stepDetail: elements.stepDetail.value,
      stepZone: elements.stepZone.value,
      stepCapability: elements.stepCapability.value,
      stepArguments: elements.stepArguments.value,
      relatedEventText: elements.relatedEventText.value,
      maxTurns: elements.maxTurns.value,
      bufferSeconds: elements.bufferSeconds.value,
      interruptAfterTurn: elements.interruptAfterTurn.value,
      nextStepScheduledFor: elements.nextStepScheduledFor.value,
      roleplayName: elements.roleplayName.value,
      roleplaySoul: elements.roleplaySoul.value,
      roleplayPlanContext: elements.roleplayPlanContext.value,
      roleplayContextEntries: elements.roleplayContextEntries.value,
      roleplayExtraInstructions: elements.roleplayExtraInstructions.value,
      roleplayRegisteredContacts: elements.roleplayRegisteredContacts.value,
    };
  }

  function persistFormState() {
    try {
      window.localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(collectFormState()));
    } catch (error) {
      return;
    }
  }

  function restoreFormState() {
    const stored = readStoredFormState();
    if (!stored) {
      return;
    }
    elements.stepTitle.value = stored.stepTitle ?? elements.stepTitle.value;
    elements.stepDetail.value = stored.stepDetail ?? elements.stepDetail.value;
    elements.stepZone.value = stored.stepZone ?? elements.stepZone.value;
    elements.stepArguments.value = stored.stepArguments ?? elements.stepArguments.value;
    elements.relatedEventText.value = stored.relatedEventText ?? elements.relatedEventText.value;
    elements.maxTurns.value = stored.maxTurns ?? elements.maxTurns.value;
    elements.bufferSeconds.value = stored.bufferSeconds ?? elements.bufferSeconds.value;
    elements.interruptAfterTurn.value = stored.interruptAfterTurn ?? elements.interruptAfterTurn.value;
    elements.nextStepScheduledFor.value = stored.nextStepScheduledFor ?? elements.nextStepScheduledFor.value;
    elements.roleplayName.value = stored.roleplayName ?? elements.roleplayName.value;
    elements.roleplaySoul.value = stored.roleplaySoul ?? elements.roleplaySoul.value;
    elements.roleplayPlanContext.value = stored.roleplayPlanContext ?? elements.roleplayPlanContext.value;
    elements.roleplayContextEntries.value =
      stored.roleplayContextEntries ?? elements.roleplayContextEntries.value;
    elements.roleplayExtraInstructions.value =
      stored.roleplayExtraInstructions ?? elements.roleplayExtraInstructions.value;
    elements.roleplayRegisteredContacts.value =
      stored.roleplayRegisteredContacts ?? elements.roleplayRegisteredContacts.value;
  }

  function toIsoOrNull(value) {
    if (!value) {
      return null;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      throw new Error("下一个执行单元开始时间格式不正确。");
    }
    return date.toISOString();
  }

  function localizeZone(value) {
    const table = {
      auto: "自动",
      real: "真实执行",
      non_real: "非真实执行",
    };
    return table[value] || value || "未知";
  }

  function localizeStopReason(value) {
    const table = {
      natural_stop: "自然停止",
      max_rounds: "达到最大轮次",
      buffer_exhausted: "缓冲时间耗尽",
      external_interrupt: "外部中断",
      proactive_interaction: "切换到主动触达",
    };
    return table[value] || value || "未知";
  }

  function appendStreamLine(text) {
    const current = elements.streamLog.textContent || "";
    elements.streamLog.textContent = current === "等待开始…" ? text : `${current}\n${text}`;
    elements.streamLog.scrollTop = elements.streamLog.scrollHeight;
  }

  function resetOutput() {
    completed = false;
    streamTurns = [];
    elements.streamLog.textContent = "正在准备执行…";
    elements.resultSummary.innerHTML =
      '<div class="placeholder-card">正在执行中，结果会在流式事件完成后汇总到这里。</div>';
    elements.turnList.innerHTML =
      '<div class="placeholder-card">等待流式事件到达…</div>';
    elements.toolInvocations.textContent = "[]";
    elements.executionTrace.textContent = "[]";
  }

  function setStreamingState(streaming) {
    isStreaming = streaming;
    elements.loadDefaults.disabled = streaming;
    elements.runLab.disabled = streaming;
    elements.stopLab.disabled = !streaming;
  }

  function renderTools(tools) {
    const stored = readStoredFormState();
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
      : '<div class="placeholder-card">当前没有可用工具。</div>';

    elements.stepCapability.innerHTML = [
      '<option value="">自动解析</option>',
      ...cachedTools.map(
        (tool) => `<option value="${escapeHtml(tool.name)}">${escapeHtml(tool.name)}</option>`
      ),
    ].join("");
    if (stored?.stepCapability && cachedTools.some((tool) => tool.name === stored.stepCapability)) {
      elements.stepCapability.value = stored.stepCapability;
    }
  }

  function applyDefaults(payload) {
    if (!payload) {
      return;
    }
    if (!elements.stepTitle.value.trim() && payload.suggested_title) {
      elements.stepTitle.value = payload.suggested_title;
    }
    if (!elements.stepDetail.value.trim() && payload.suggested_detail) {
      elements.stepDetail.value = payload.suggested_detail;
    }
    if (!elements.roleplaySoul.value.trim() && payload.roleplay?.soul_md) {
      elements.roleplaySoul.value = payload.roleplay.soul_md;
    }
    if (!elements.roleplayRegisteredContacts.value.trim() && payload.roleplay?.registered_contacts) {
      elements.roleplayRegisteredContacts.value = payload.roleplay.registered_contacts;
    }
    renderTools(payload.tools || []);
    persistFormState();
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
        registered_contacts: elements.roleplayRegisteredContacts.value,
      },
    };
  }

  function renderSummary(payload) {
    const proactive = payload.raw_data?.proactive_interaction || null;
    const cards = [
      `
        <article class="summary-card">
          <h3>停止原因</h3>
          <p>${escapeHtml(localizeStopReason(payload.stop_reason))}</p>
          <p class="meta">resolved zone: ${escapeHtml(localizeZone(payload.resolved_zone))}</p>
        </article>
      `,
      `
        <article class="summary-card">
          <h3>初始场景</h3>
          <p>${escapeHtml(payload.initial_scene || "")}</p>
        </article>
      `,
      `
        <article class="summary-card">
          <h3>最终场景</h3>
          <p>${escapeHtml(payload.final_scene || "")}</p>
        </article>
      `,
      `
        <article class="summary-card">
          <h3>最终结果</h3>
          <p>${escapeHtml(payload.final_result || "")}</p>
          <p class="meta">capability: ${escapeHtml(payload.resolved_capability || "自动")}</p>
        </article>
      `,
    ];

    if (proactive?.name || proactive?.message_content) {
      cards.push(`
        <article class="summary-card">
          <h3>主动触达 Handoff</h3>
          <p><strong>对象：</strong>${escapeHtml(proactive.name || "")}</p>
          <p><strong>首条消息：</strong>${escapeHtml(proactive.message_content || "")}</p>
        </article>
      `);
    }

    elements.resultSummary.innerHTML = cards.join("");
  }

  function renderParagraphs(parts) {
    return parts
      .filter(Boolean)
      .map((part) => `<p>${escapeHtml(part)}</p>`)
      .join("");
  }

  function renderMessageBubble({ speaker, label, bodyHtml, extraHtml = "" }) {
    return `
      <div class="message-row ${speaker}">
        <article class="message-bubble ${speaker}">
          <p class="message-label">${escapeHtml(label)}</p>
          <div class="message-body">
            ${bodyHtml}
            ${extraHtml}
          </div>
        </article>
      </div>
    `;
  }

  function renderExecutorEventsBlock(events) {
    if (!Array.isArray(events) || !events.length) {
      return "";
    }
    return `
      <section class="executor-events-block">
        <p class="executor-events-title">Executor 事件流</p>
        <pre class="code-block compact executor-events-code">${escapeHtml(JSON.stringify(events, null, 2))}</pre>
      </section>
    `;
  }

  function renderExecutorEventsBlock(events) {
    if (!Array.isArray(events) || !events.length) {
      return "";
    }
    const serialized = escapeHtml(JSON.stringify(events, null, 2));
    return `
      <details class="executor-events-block">
        <summary class="executor-events-summary">
          <span class="executor-events-title">Executor 事件流</span>
          <span class="executor-events-count">${events.length} 条</span>
        </summary>
        <pre class="code-block compact executor-events-code">${serialized}</pre>
      </details>
    `;
  }

  function renderTurns(turns, finalStopReason = null) {
    if (!Array.isArray(turns) || !turns.length) {
      elements.turnList.innerHTML =
        '<div class="placeholder-card">这一轮没有留下中间轮次，通常表示一开始就自然停止了。</div>';
      return;
    }
    elements.turnList.innerHTML = turns
      .map((turn, index) => {
        const isInitial = turn.turn_index === 0;
        const hasExecutorFollowUp = Boolean(turn.next_scene || turn.next_result);
        const hasExecutorRaw =
          !!(turn.executor_raw_output && Object.keys(turn.executor_raw_output).length);
        const hasExecutorEvents =
          Array.isArray(turn.executor_events) && turn.executor_events.length > 0;
        const roleplayLabel = isInitial ? "Roleplay 初始输入" : "Roleplay 回复";
        const roleplayBubble = renderMessageBubble({
          speaker: "roleplay",
          label: roleplayLabel,
          bodyHtml: renderParagraphs([turn.roleplay_response || ""]),
        });

        const executorParts = isInitial
          ? [turn.scene || "", turn.result || ""]
          : [
              turn.next_scene || turn.executor_raw_output?.scene || "",
              turn.next_result || turn.executor_raw_output?.result || "",
            ];
        const executorRawHtml =
          turn.executor_raw_output && Object.keys(turn.executor_raw_output).length
            ? `<pre class="code-block compact">${escapeHtml(
                JSON.stringify(turn.executor_raw_output, null, 2)
              )}</pre>`
            : "";
        const executorEventsHtml = renderExecutorEventsBlock(turn.executor_events || []);

        let executorBubble = "";
        if (isInitial || hasExecutorFollowUp || hasExecutorRaw || hasExecutorEvents) {
          executorBubble = renderMessageBubble({
            speaker: "executor",
            label: isInitial ? "Executor 首轮输出" : "Executor 输出",
            bodyHtml: renderParagraphs(executorParts),
            extraHtml: `${executorEventsHtml}${executorRawHtml}`,
          });
        } else if (turn.stop_reason === "proactive_interaction") {
          executorBubble = `
            <div class="message-row system">
              <div class="message-meta-note">
                这一轮没有继续执行下一次 executor 输出，而是切换到了主动触达：
                ${escapeHtml(turn.handoff_payload?.name || "")}
              </div>
            </div>
          `;
        } else {
          executorBubble = `
            <div class="message-row system">
              <div class="message-meta-note">这一轮后自然停止，Executor 没有继续推进下一条输出。</div>
            </div>
          `;
        }

        const stopReason = turn.stop_reason || (index === turns.length - 1 ? finalStopReason : null);
        return `
          <article class="turn-card dialogue-turn">
            <div class="turn-meta">
              <span class="pill">${isInitial ? "初始轮" : `第 ${escapeHtml(String(turn.turn_index))} 轮`}</span>
              <span class="pill zone">${escapeHtml(localizeZone(turn.zone))}</span>
              <span class="pill stop">${escapeHtml(localizeStopReason(stopReason || "继续推进"))}</span>
            </div>
            <div class="dialogue-thread">
              ${roleplayBubble}
              ${executorBubble}
            </div>
          </article>
        `;
      })
      .join("");
  }

  function handleStreamEvent(payload) {
    if (!payload || !payload.event) {
      return;
    }
    switch (payload.event) {
      case "started":
        appendStreamLine(`[started] ${payload.data.title} | ${localizeZone(payload.data.zone)}`);
        if (Array.isArray(payload.data.registered_contacts) && payload.data.registered_contacts.length) {
          appendStreamLine(`[contacts] ${payload.data.registered_contacts.map((item) => item.name).join(", ")}`);
        }
        showMessage("Executor loop 已开始，正在流式输出。", "info", true);
        break;
      case "initial_turn":
        appendStreamLine(
          `[initial] zone=${localizeZone(payload.data.resolved_zone)} capability=${payload.data.resolved_capability || "auto"}`
        );
        appendStreamLine(`[initial roleplay] ${payload.data.roleplay_response || ""}`);
        appendStreamLine(`[initial scene] ${payload.data.scene || ""}`);
        appendStreamLine(`[initial result] ${payload.data.result || ""}`);
        if (payload.data.executor_raw_output) {
          appendStreamLine(`[initial executor raw] ${JSON.stringify(payload.data.executor_raw_output)}`);
        }
        if (Array.isArray(payload.data.executor_events) && payload.data.executor_events.length) {
          appendStreamLine(`[initial executor events] ${JSON.stringify(payload.data.executor_events)}`);
        }
        break;
      case "phase":
        appendStreamLine(
          `[phase] ${payload.data.label || "working"}${payload.data.turn_index ? ` @turn ${payload.data.turn_index}` : ""}`
        );
        break;
      case "roleplay_response":
        appendStreamLine(`[roleplay ${payload.data.turn_index}] ${payload.data.content || ""}`);
        break;
      case "executor_agent_event":
        appendStreamLine(`[executor event] ${JSON.stringify(payload.data)}`);
        break;
      case "proactive_interaction":
        appendStreamLine(
          `[proactive ${payload.data.turn_index}] ${payload.data.name || "unknown"} <- ${payload.data.message_content || ""}`
        );
        break;
      case "turn_record":
        streamTurns.push(payload.data.turn);
        renderTurns(streamTurns);
        appendStreamLine(`[turn ${payload.data.turn.turn_index}] turn recorded`);
        if (payload.data.turn.next_scene) {
          appendStreamLine(`[executor ${payload.data.turn.turn_index}] scene: ${payload.data.turn.next_scene}`);
        }
        if (payload.data.turn.next_result) {
          appendStreamLine(`[executor ${payload.data.turn.turn_index}] result: ${payload.data.turn.next_result}`);
        }
        if (payload.data.turn.executor_raw_output && Object.keys(payload.data.turn.executor_raw_output).length) {
          appendStreamLine(`[executor ${payload.data.turn.turn_index} raw] ${JSON.stringify(payload.data.turn.executor_raw_output)}`);
        }
        if (Array.isArray(payload.data.turn.executor_events) && payload.data.turn.executor_events.length) {
          appendStreamLine(`[executor ${payload.data.turn.turn_index} events] ${JSON.stringify(payload.data.turn.executor_events)}`);
        }
        break;
      case "loop_stop":
        appendStreamLine(`[stop] ${localizeStopReason(payload.data.stop_reason)}`);
        if (payload.data.proactive_interaction?.name) {
          appendStreamLine(
            `[handoff] ${payload.data.proactive_interaction.name}: ${payload.data.proactive_interaction.message_content || ""}`
          );
        }
        break;
      case "completed":
        completed = true;
        renderFinal(payload.data.response);
        showMessage("Executor Lab 已跑完这一轮。", "success");
        appendStreamLine("[completed] final response received");
        break;
      case "error":
        appendStreamLine(`[error] ${payload.data.detail || "unknown error"}`);
        showMessage(payload.data.detail || "执行失败。", "error");
        break;
      default:
        appendStreamLine(`[${payload.event}] ${formatJson(payload.data)}`);
        break;
    }
  }

  function renderFinal(result) {
    renderSummary(result);
    renderTurns(result.turns || [], result.stop_reason || null);
    elements.toolInvocations.textContent = formatJson(result.tool_invocations || []);
    elements.executionTrace.textContent = formatJson(result.execution_trace || []);
  }

  async function loadDefaults() {
    const payload = await request("/api/executor-lab/defaults");
    applyDefaults(payload);
    showMessage("工具列表已加载。", "success");
  }

  async function runLab() {
    resetOutput();
    const payload = buildPayload();
    const controller = new AbortController();
    currentController = controller;
    setStreamingState(true);

    try {
      const response = await fetch("/api/executor-lab/run/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Request failed: ${response.status}`);
      }
      if (!response.body) {
        throw new Error("浏览器没有拿到可读的流式响应。");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) {
            continue;
          }
          handleStreamEvent(JSON.parse(trimmed));
        }
        if (done) {
          break;
        }
      }

      if (buffer.trim()) {
        handleStreamEvent(JSON.parse(buffer.trim()));
      }
      if (!completed) {
        throw new Error("流已结束，但没有收到 completed 事件。");
      }
    } finally {
      if (currentController === controller) {
        currentController = null;
      }
      setStreamingState(false);
    }
  }

  function stopLab() {
    if (!currentController || !isStreaming) {
      return;
    }
    appendStreamLine("[stopped] 已请求停止调试，正在中断当前流。");
    showMessage("已请求停止调试。", "info", true);
    currentController.abort();
  }

  async function safe(action) {
    try {
      await action();
    } catch (error) {
      if (error?.name === "AbortError") {
        showMessage("调试已停止。", "info");
        return;
      }
      showMessage(error.message || "操作失败。", "error");
      appendStreamLine(`[error] ${error.message || "操作失败。"}`);
    }
  }

  function bindEvents() {
    Object.values(elements).forEach((element) => {
      if (!element || !(element instanceof HTMLElement)) {
        return;
      }
      const tagName = element.tagName;
      if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT") {
        element.addEventListener("input", persistFormState);
        element.addEventListener("change", persistFormState);
      }
    });
    elements.loadDefaults.addEventListener("click", () => void safe(loadDefaults));
    elements.runLab.addEventListener("click", () => void safe(runLab));
    elements.stopLab.addEventListener("click", stopLab);
    window.addEventListener("pagehide", persistFormState);
  }

  async function init() {
    restoreFormState();
    bindEvents();
    setStreamingState(false);
    await safe(loadDefaults);
  }

  void init();
})();
