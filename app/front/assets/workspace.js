(function () {
  const elements = {
    navLinks: Array.from(document.querySelectorAll(".nav-link")),
    sectionTitle: document.getElementById("section-title"),
    sidebarPersona: document.getElementById("sidebar-persona"),
    sidebarStatus: document.getElementById("sidebar-status"),
    refreshAll: document.getElementById("refresh-all"),
    pageMessage: document.getElementById("page-message"),

    personaList: document.getElementById("persona-list"),
    personaCreateForm: document.getElementById("persona-create-form"),
    personaCreateName: document.getElementById("persona-create-name"),
    personaName: document.getElementById("persona-name"),
    personaKey: document.getElementById("persona-key"),
    personaSoul: document.getElementById("persona-soul"),
    personaSave: document.getElementById("persona-save"),
    personaActivate: document.getElementById("persona-activate"),
    personaDelete: document.getElementById("persona-delete"),
    personaDebug: document.getElementById("persona-debug"),

    workbenchSummary: document.getElementById("workbench-summary"),
    virtualTimeDisplay: document.getElementById("virtual-time-display"),
    virtualTimeInput: document.getElementById("virtual-time-input"),
    virtualTimeApply: document.getElementById("virtual-time-apply"),
    planList: document.getElementById("plan-list"),
    workbenchDebug: document.getElementById("workbench-debug"),

    chatFeed: document.getElementById("chat-feed"),
    chatUserName: document.getElementById("chat-user-name"),
    chatUserId: document.getElementById("chat-user-id"),
    chatChannel: document.getElementById("chat-channel"),
    chatInput: document.getElementById("chat-input"),
    chatSend: document.getElementById("chat-send"),
    chatDebug: document.getElementById("chat-debug"),

    toolsList: document.getElementById("tools-list"),
    mcpList: document.getElementById("mcp-list"),
    settingsDebug: document.getElementById("settings-debug"),
  };

  const state = {
    activeSection: "personas",
    personas: [],
    activePersonaKey: null,
    selectedPersonaKey: null,
    selectedPersonaDetail: null,
    workbench: null,
    chat: null,
    tools: null,
    health: null,
    clockAnchorServerMs: null,
    clockAnchorClientMs: null,
    timers: {
      clock: null,
      poll: null,
    },
  };

  function showMessage(text, kind = "info") {
    elements.pageMessage.textContent = text;
    elements.pageMessage.className = "page-message";
    if (kind === "error") {
      elements.pageMessage.style.border = "1px solid rgba(156, 47, 47, 0.24)";
    } else {
      elements.pageMessage.style.border = "1px solid rgba(94, 76, 51, 0.14)";
    }
    window.clearTimeout(showMessage._timer);
    showMessage._timer = window.setTimeout(() => {
      elements.pageMessage.className = "page-message hidden";
    }, 3600);
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

  function formatTime(value) {
    if (!value) {
      return "--";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function runtimeStatusLabel(value) {
    const map = {
      booting: "启动中",
      idle: "空闲",
      waiting: "等待中",
      processing: "执行中",
      paused: "已暂停",
      error: "异常",
    };
    return map[value] || value || "未知";
  }

  function outcomeStatusLabel(value) {
    const map = {
      success: "成功",
      partial_success: "部分成功",
      retryable_failure: "可重试失败",
      blocked_failure: "阻塞失败",
    };
    return map[value] || value || "未知";
  }

  function activeSectionTitle(section) {
    const map = {
      personas: "角色",
      workbench: "工作台",
      chat: "聊天",
      settings: "设置",
    };
    return map[section] || "工作台";
  }

  function setActiveSection(section) {
    state.activeSection = section;
    elements.sectionTitle.textContent = activeSectionTitle(section);
    document.querySelectorAll(".view-section").forEach((node) => {
      node.classList.toggle("active", node.id === `section-${section}`);
    });
    elements.navLinks.forEach((button) => {
      button.classList.toggle("active", button.dataset.section === section);
    });
  }

  function summaryCard(title, body, extra = "") {
    return `
      <article class="summary-card">
        <h4>${escapeHtml(title)}</h4>
        <p>${escapeHtml(body)}</p>
        ${extra ? `<p>${escapeHtml(extra)}</p>` : ""}
      </article>
    `;
  }

  function renderSidebar() {
    const personaName =
      state.selectedPersonaDetail?.card?.name ||
      state.workbench?.persona_name ||
      "未选择角色";
    elements.sidebarPersona.textContent = `当前角色：${personaName}`;
    const status = state.workbench?.summary?.runtime_status || "";
    elements.sidebarStatus.textContent = `状态：${runtimeStatusLabel(status)}`;
  }

  function renderPersonas() {
    if (!state.personas.length) {
      elements.personaList.innerHTML = '<div class="empty-state">还没有角色，先创建一个吧。</div>';
      elements.personaDebug.textContent = formatJson({});
      return;
    }

    elements.personaList.innerHTML = state.personas
      .map((card) => {
        const active = card.persona_key === state.activePersonaKey;
        const current = card.persona_key === state.selectedPersonaKey;
        const classes = [
          "persona-card",
          active ? "active-card" : "",
          current ? "current-card" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return `
          <button class="${classes}" type="button" data-persona-key="${escapeHtml(card.persona_key)}">
            <div class="card-title-row">
              <strong>${escapeHtml(card.name)}</strong>
              ${active ? '<span class="pill accent">活动中</span>' : ""}
            </div>
            <p>${escapeHtml(card.persona_key)}</p>
          </button>
        `;
      })
      .join("");

    elements.personaDebug.textContent = formatJson({
      active_persona_key: state.activePersonaKey,
      selected_persona: state.selectedPersonaDetail,
    });
  }

  function renderPersonaDetail() {
    const detail = state.selectedPersonaDetail;
    if (!detail) {
      elements.personaName.value = "";
      elements.personaKey.value = "";
      elements.personaSoul.value = "";
      return;
    }
    elements.personaName.value = detail.card?.name || "";
    elements.personaKey.value = detail.card?.persona_key || "";
    elements.personaSoul.value = detail.soul_md || "";
    elements.personaDebug.textContent = formatJson(detail);
  }

  function renderWorkbench() {
    const payload = state.workbench;
    if (!payload) {
      elements.workbenchSummary.innerHTML = '<div class="empty-state">工作台数据尚未加载。</div>';
      elements.planList.innerHTML = '<div class="empty-state">暂无计划。</div>';
      return;
    }

    const summary = payload.summary || {};
    elements.workbenchSummary.innerHTML = [
      summaryCard("活动角色", payload.persona_name || "未设置"),
      summaryCard("运行状态", runtimeStatusLabel(summary.runtime_status), summary.plan_summary || "暂无计划摘要"),
      summaryCard("下次唤醒", formatTime(summary.next_wake_at || payload.state?.interaction_cooldown_until || "")),
      summaryCard("上次结果", outcomeStatusLabel(summary.last_outcome_status || ""), summary.last_error || ""),
    ].join("");

    elements.workbenchDebug.textContent = formatJson(payload);

    if (!payload.plan_items?.length) {
      elements.planList.innerHTML = '<div class="empty-state">当前还没有可显示的计划项。</div>';
      return;
    }

    elements.planList.innerHTML = payload.plan_items
      .map((item) => {
        const classes = [
          "plan-card",
          item.active ? "active-card" : "",
          item.current ? "current-card" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const recordHtml = (item.execution_records || []).length
          ? item.execution_records
              .map(
                (record) => `
                  <div class="execution-record">
                    <h5>${escapeHtml(formatTime(record.recorded_at))}</h5>
                    <p>${escapeHtml(record.summary)}</p>
                    <div class="meta-row">
                      <span class="pill">${escapeHtml(outcomeStatusLabel(record.status))}</span>
                      ${record.stop_reason ? `<span class="pill">${escapeHtml(record.stop_reason)}</span>` : ""}
                    </div>
                    <details class="debug-panel">
                      <summary>执行细节</summary>
                      <pre class="code-block">${escapeHtml(formatJson(record))}</pre>
                    </details>
                  </div>
                `
              )
              .join("")
          : '<div class="execution-record"><p>还没有执行记录。</p></div>';
        return `
          <details class="${classes}" ${item.active || item.current ? "open" : ""}>
            <summary>
              <div class="card-title-row">
                <strong>${escapeHtml(item.time_label || "未定时段")} · ${escapeHtml(item.label)}</strong>
                <div class="meta-row">
                  ${item.active ? '<span class="pill accent">当前时段</span>' : ""}
                  ${item.current ? '<span class="pill green">正在执行</span>' : ""}
                  <span class="pill">${escapeHtml(item.status)}</span>
                </div>
              </div>
            </summary>
            ${recordHtml}
          </details>
        `;
      })
      .join("");
  }

  function renderChat() {
    const payload = state.chat;
    if (!payload || !payload.entries?.length) {
      elements.chatFeed.innerHTML = '<div class="empty-state">还没有聊天记录。</div>';
      elements.chatDebug.textContent = formatJson(payload || {});
      return;
    }

    elements.chatFeed.innerHTML = payload.entries
      .map((entry) => {
        const classes = ["chat-card", entry.direction === "incoming" ? "incoming" : "outgoing"].join(" ");
        return `
          <article class="${classes}">
            <div class="card-title-row">
              <strong>${escapeHtml(entry.speaker || "消息")}</strong>
              <span class="pill">${escapeHtml(formatTime(entry.created_at))}</span>
            </div>
            <div class="meta-row">
              <span class="pill">${escapeHtml(entry.channel || "api")}</span>
              ${entry.partner_name ? `<span class="pill">${escapeHtml(entry.partner_name)}</span>` : ""}
              ${entry.direction ? `<span class="pill">${escapeHtml(entry.direction)}</span>` : ""}
            </div>
            <div class="chat-content">${escapeHtml(entry.content || entry.raw_content)}</div>
          </article>
        `;
      })
      .join("");
    elements.chatDebug.textContent = formatJson(payload);
    elements.chatFeed.scrollTop = elements.chatFeed.scrollHeight;
  }

  function renderSettings() {
    const tools = state.tools;
    const health = state.health;

    elements.toolsList.innerHTML = tools?.tools?.length
      ? tools.tools
          .map(
            (tool) => `
              <article class="tool-card">
                <div class="card-title-row">
                  <strong>${escapeHtml(tool.name)}</strong>
                  <span class="pill">${escapeHtml(tool.source_type || "internal")}</span>
                </div>
                <p>${escapeHtml(tool.description || "暂无描述")}</p>
              </article>
            `
          )
          .join("")
      : '<div class="empty-state">当前没有可显示的工具。</div>';

    elements.mcpList.innerHTML = health?.mcp_servers?.length
      ? health.mcp_servers
          .map(
            (server) => `
              <article class="mcp-card">
                <div class="card-title-row">
                  <strong>${escapeHtml(server.server_id)}</strong>
                  <span class="pill ${server.connected ? "green" : "danger"}">${server.connected ? "已连接" : "未连接"}</span>
                </div>
                <p>transport: ${escapeHtml(server.transport)}</p>
                <p>tools: ${escapeHtml(String(server.tool_count))}</p>
              </article>
            `
          )
          .join("")
      : '<div class="empty-state">当前没有 MCP 服务器信息。</div>';

    elements.settingsDebug.textContent = formatJson({
      health,
      tools,
    });
  }

  function updateClockDisplay() {
    if (!state.clockAnchorServerMs) {
      elements.virtualTimeDisplay.textContent = "--";
      return;
    }
    const summary = state.workbench?.summary || {};
    const nowMs =
      summary.clock_mode === "real_time"
        ? state.clockAnchorServerMs + (Date.now() - state.clockAnchorClientMs)
        : state.clockAnchorServerMs;
    elements.virtualTimeDisplay.textContent = formatTime(new Date(nowMs).toISOString());
  }

  function syncClockAnchor() {
    const currentTime = state.workbench?.summary?.current_time;
    if (!currentTime) {
      return;
    }
    const parsed = new Date(currentTime).getTime();
    if (Number.isNaN(parsed)) {
      return;
    }
    state.clockAnchorServerMs = parsed;
    state.clockAnchorClientMs = Date.now();
    const inputValue = new Date(parsed - new Date().getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
    if (!document.activeElement || document.activeElement !== elements.virtualTimeInput) {
      elements.virtualTimeInput.value = inputValue;
    }
    updateClockDisplay();
  }

  async function loadPersonaCards() {
    const payload = await request("/api/personas");
    state.personas = payload.cards || [];
    state.activePersonaKey = payload.active_persona_key || null;
    if (!state.selectedPersonaKey) {
      state.selectedPersonaKey = state.activePersonaKey || state.personas[0]?.persona_key || null;
    }
    renderPersonas();
  }

  async function loadPersonaDetail(personaKey) {
    if (!personaKey) {
      state.selectedPersonaKey = null;
      state.selectedPersonaDetail = null;
      renderPersonaDetail();
      return;
    }
    state.selectedPersonaKey = personaKey;
    state.selectedPersonaDetail = await request(`/api/personas/${encodeURIComponent(personaKey)}`);
    renderPersonas();
    renderPersonaDetail();
    renderSidebar();
  }

  async function refreshWorkbench() {
    state.workbench = await request("/api/workspace/workbench");
    syncClockAnchor();
    renderWorkbench();
    renderSidebar();
  }

  async function refreshChat() {
    state.chat = await request("/api/workspace/chat?limit=120");
    renderChat();
  }

  async function refreshSettings() {
    const [tools, health] = await Promise.all([request("/api/tools/debug"), request("/health")]);
    state.tools = tools;
    state.health = health;
    renderSettings();
  }

  async function refreshAll() {
    await loadPersonaCards();
    if (state.selectedPersonaKey) {
      await loadPersonaDetail(state.selectedPersonaKey);
    } else {
      renderPersonaDetail();
    }
    await Promise.all([refreshWorkbench(), refreshChat(), refreshSettings()]);
  }

  async function handleCreatePersona(event) {
    event.preventDefault();
    const name = elements.personaCreateName.value.trim();
    if (!name) {
      throw new Error("请输入新角色名称。");
    }
    const payload = await request("/api/personas", {
      method: "POST",
      body: JSON.stringify({ name, activate: false }),
    });
    elements.personaCreateName.value = "";
    state.selectedPersonaKey = payload.card?.persona_key || null;
    showMessage("角色已创建。");
    await refreshAll();
  }

  async function handleSaveSoul() {
    if (!state.selectedPersonaKey) {
      throw new Error("请先选择一个角色。");
    }
    await request(`/api/personas/${encodeURIComponent(state.selectedPersonaKey)}/soul`, {
      method: "PUT",
      body: JSON.stringify({ soul_md: elements.personaSoul.value }),
    });
    showMessage("soul.md 已保存。");
    await loadPersonaDetail(state.selectedPersonaKey);
    await refreshWorkbench();
    await refreshChat();
  }

  async function handleActivatePersona() {
    if (!state.selectedPersonaKey) {
      throw new Error("请先选择一个角色。");
    }
    await request(`/api/personas/${encodeURIComponent(state.selectedPersonaKey)}/activate`, {
      method: "POST",
    });
    showMessage("已切换活动角色。");
    await refreshAll();
  }

  async function handleDeletePersona() {
    if (!state.selectedPersonaKey) {
      throw new Error("请先选择一个角色。");
    }
    const ok = window.confirm(`确定删除角色 ${state.selectedPersonaKey} 吗？`);
    if (!ok) {
      return;
    }
    await request(`/api/personas/${encodeURIComponent(state.selectedPersonaKey)}`, {
      method: "DELETE",
    });
    state.selectedPersonaKey = null;
    state.selectedPersonaDetail = null;
    showMessage("角色已删除。");
    await refreshAll();
  }

  async function handleApplyVirtualTime() {
    if (!elements.virtualTimeInput.value) {
      throw new Error("请先输入虚拟时间。");
    }
    const iso = new Date(elements.virtualTimeInput.value).toISOString();
    await request("/api/runtime/clock/set", {
      method: "POST",
      body: JSON.stringify({ at: iso }),
    });
    showMessage("虚拟时间已更新。");
    await refreshWorkbench();
  }

  async function handleSendMessage() {
    const text = elements.chatInput.value.trim();
    if (!text) {
      throw new Error("请输入消息内容。");
    }
    await request("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        user_id: elements.chatUserId.value.trim() || "default-user",
        user_name: elements.chatUserName.value.trim() || "用户",
        channel: elements.chatChannel.value.trim() || "api",
        text,
      }),
    });
    elements.chatInput.value = "";
    showMessage("消息已发送。");
    await Promise.all([refreshWorkbench(), refreshChat()]);
  }

  function bindEvents() {
    elements.navLinks.forEach((button) => {
      button.addEventListener("click", () => {
        setActiveSection(button.dataset.section || "personas");
      });
    });

    elements.refreshAll.addEventListener("click", () => void safe(refreshAll));
    elements.personaCreateForm.addEventListener("submit", (event) => void safe(() => handleCreatePersona(event)));
    elements.personaSave.addEventListener("click", () => void safe(handleSaveSoul));
    elements.personaActivate.addEventListener("click", () => void safe(handleActivatePersona));
    elements.personaDelete.addEventListener("click", () => void safe(handleDeletePersona));
    elements.virtualTimeApply.addEventListener("click", () => void safe(handleApplyVirtualTime));
    elements.chatSend.addEventListener("click", () => void safe(handleSendMessage));
    elements.personaList.addEventListener("click", (event) => {
      const target = event.target instanceof HTMLElement ? event.target.closest("[data-persona-key]") : null;
      if (!target) {
        return;
      }
      void safe(() => loadPersonaDetail(target.getAttribute("data-persona-key")));
    });
  }

  async function safe(action) {
    try {
      await action();
    } catch (error) {
      showMessage(error.message || "操作失败。", "error");
    }
  }

  function startPolling() {
    window.clearInterval(state.timers.clock);
    window.clearInterval(state.timers.poll);
    state.timers.clock = window.setInterval(updateClockDisplay, 1000);
    state.timers.poll = window.setInterval(() => {
      void safe(async () => {
        await Promise.all([refreshWorkbench(), refreshChat(), refreshSettings()]);
      });
    }, 5000);
  }

  async function init() {
    bindEvents();
    setActiveSection(state.activeSection);
    await safe(refreshAll);
    startPolling();
  }

  void init();
})();
