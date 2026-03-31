# Blueprint
MVP只追求流程跑通，落地代码的时候请避免过度设计和额外兜底方案。
## 文档定位

`blueprint.md` 是面向实现的压缩蓝图，不是新的总设计文档。

三层分工：

- `README.md`：高层设计、产品方向、架构原意、MVP边界的源头文件。
- `blueprint.md`：从 `README.md` 抽取出的实现契约、模块映射、关键边界。
- `task_plan.md / findings.md / progress.md`：当前阶段、发现、进度和验证记录。

使用规则：

- 日常编码默认先读本文件，再读 planning files。
- 需要确认原始设计意图、边界变化、或 blueprint 与代码冲突时，再回读 `README.md`。
- 如果本文件与 `README.md` 冲突，以 `README.md` 为准。
- 本文件只保留“会影响代码”的规范性内容，不重复展开叙事例子。

Source snapshot：

- Source file: `README.md`
- Source path: `C:\Users\lenovo\Desktop\agent\Amadeus\README.md`
- Synced against current working tree on `2026-03-26`
- Line-number policy: 行号是当前工作树快照，引用时同时保留章节标题，避免 README 后续编辑导致纯行号失效。

## 项目目标

核心目标：

`输入人设描述 -> 输出符合该人设的连续生活轨迹与互动行为`

实现对象不是单轮聊天机器人，而是一个可持续运行的人格驱动行为系统。

Source:

- `## 项目定位`，`README.md` lines `4-15`

## MVP硬边界

当前实现必须围绕以下约束展开：

- 先支持 `1 个角色`
- 先支持 `1 个真实用户`
- 先接入 `1 个消息渠道`
- 先做 `MCP 兼容层`
- 先做一版记忆架构
- 系统优先保证：`连续`、`自洽`、`可回溯`、`可扩展`

第一阶段最小闭环至少包括：

- 人设初始化
- 日计划与小时计划
- 分钟级动作执行
- 执行后 replan
- 记忆写入
- 对外消息回复

当前不作为一期硬门槛的内容：

- `主动触达`
- heartbeat 式空闲自驱兜底
- `Archive Memory` 完整实现
- 多用户与 `agent -> agent`
- 第一版就覆盖全部复杂能力

Source:

- `## MVP设计原则/边界`，`README.md` lines `51-87`
- `### MVP 运行驱动说明`，`README.md` lines `88-95`
- `### 3. 交互层`，`README.md` lines `338-357`
- `### 四层记忆架构`，`README.md` lines `396-466`

## 运行与规划约束

运行时：

- MVP 是 `事件驱动` 的，不是 `heartbeat 驱动` 的。
- 有明确分钟级计划时，由计划时间表推进。
- 无明确动作时，系统可以保持安静，直到下一个计划节点或外部事件。

规划：

- `00:00` 或首次启动时生成“今日摘要计划”。
- 每次只对最近的一个计划做小时计划展开。
- 每次只展开未来 `5-15 分钟` 的动作。
- 小时计划负责限定当前时间窗方向。
- 分钟级动作负责真实执行与调度。
- 调度器按下一个分钟级动作的计划时间点唤醒。
- 真正执行的是分钟级动作，不是日计划或小时计划本身。

Source:

- `### MVP 运行驱动说明`，`README.md` lines `88-95`
- `## 规划策略`，`README.md` lines `96-120`
- `### 2. 生活运行层(运行层)`，`README.md` lines `143-318`

## 架构总览

系统按五块理解：

- 角色层：这个人是谁。
- 生活运行层：这个人今天怎么活。
- 交互层：这个人怎么和外界接触。
- 基础设施层：系统怎么稳定落地。
- 记忆系统：横切以上各层，负责把经历变成可追溯、可检索、可回忆的记忆。

Source:

- `## 四大层架构`，`README.md` lines `121-124`
- `## 横切子系统：记忆系统`，`README.md` lines `382-395`

## 模块映射

| 模块 | 当前代码位置 | 代码职责 | README 来源 |
|------|---------------|-----------|-------------|
| 角色层 | `app/persona/models.py` `app/persona/service.py` `memory/soul.md` | 人设初始化、补全、背景知识建模、`soul.md` 持久化 | `### 1. 角色层` `125-142` |
| Planning | `app/runtime/planning.py` `app/core/state.py` | 日/小时/分钟计划生成与分层状态维护，只展开最近窗口 | `## 规划策略` `96-120`；`### 2. 生活运行层(运行层)` 中 `Planing` `143-169` |
| Replan | `app/runtime/replan.py` `app/runtime/orchestrator.py` | 执行后或外界介入后的重规划判断与触发 | `### 2. 生活运行层(运行层)` 中 `Replan` `170-175` |
| Execution | `app/runtime/execution.py` `app/core/outcomes.py` | 执行分钟级动作、驱动有界 execution loop、归一化执行结果 | `### 2. 生活运行层(运行层)` 中 `Execution` `161-323` |
| Interaction | `app/runtime/interaction.py` `app/communication/hub.py` `app/communication/channels.py` `app/main.py` | 统一用户消息与渠道事件，转成系统可消费的 runtime event | `### 3. 交互层` `338-357` |
| Runtime orchestration | `app/runtime/orchestrator.py` `app/runtime/clock.py` `app/runtime/inspection.py` `app/runtime/scenario.py` | 调度、推进、观察、虚拟时间与场景驱动 | `### 2. 生活运行层(运行层)` `143-318`；`### 4. 基础设施层` `358-381` |
| 基础设施层 | `app/infra/env.py` `app/infra/settings.py` `app/infra/logging.py` `app/infra/storage.py` `app/infra/model_client.py` | 配置、日志、存储、模型适配与运行时基础设施 | `### 4. 基础设施层` `358-381` |
| 记忆系统 | `app/memory/models.py` `app/memory/service.py` `app/memory/retrieval.py` `app/memory/snapshots.py` `app/infra/storage.py` | Raw/Core/Active/Archive 模型、检索、快照、回放 | `## 横切子系统：记忆系统` `382-395`；`### 四层记忆架构` `396-466`；`### 记忆数据流` `467-476` |
| MCP兼容层 | `app/mcp/builtins.py` `app/mcp/compat.py` `app/mcp/registry.py` `app/mcp/schemas.py` `app/tool/registry.py` | 高层动作到 MCP 工具的映射，统一结果与错误分类 | `## MCP 兼容层职责` `477-502` |
| 模型路由 | `app/infra/model_client.py` `app/infra/settings.py` | `dialogue / decision / memory` 多路由模型接入与隔离 | `### PydanticAI 的使用原则` `535-557`；`### 多模型与多 API 路由` `558-582` |

## 不可违背的实现契约

### 命名与边界

- README 中的模块名、服务名、目录名优先理解为职责占位符，不是必须照搬的最终命名。
- 可以调整命名、拆分与组织方式，但职责边界和数据流不能偏离。

Source:

- `## 命名说明`，`README.md` lines `16-24`

### Persona 契约

- 角色层最终产物是 `soul.md`。
- `soul.md` 代表 agent 的人格根基，而不是临时对话上下文。
- 角色层需要覆盖稳定特质、长期偏好、长期关系、粗略作息、活动范围等背景知识。

Source:

- `### 1. 角色层`，`README.md` lines `125-142`

### Planning 契约

- 日计划和小时计划可以是自由文本。
- 分钟计划必须是结构化数据，因为程序要拿 `description` 与 `duration` 去执行和计时。
- 不能把全天分钟计划一次性铺满；只展开最近窗口。

Source:

- `## 规划策略`，`README.md` lines `96-120`
- `### 2. 生活运行层(运行层)`，`README.md` lines `143-318`

### Execution 契约

- `tool` 是统一上位概念，内部工具、MCP 服务、API 等都按 `tool` 看待。
- 真正执行的是分钟级动作。
- 执行语义分为 `Real Zone / Weak Real Zone / Ambiguity Zone`。
- `Real Zone` 负责有真实 tool 路径的动作，tool 返回真实数据。
- `Weak Real Zone` 负责没有 tool 但可从人设与上下文自洽推导的日常行为。
- `Ambiguity Zone` 负责没有 tool 且不能直接从人设补全细节的专业行为；需要产出 `detail elaboration` 一类中间结果，并可继续 loop 补全细节。
- 路由顺序是：先走 tool 注册表/关键词匹配；匹配到就走 `Real Zone`；匹配不到再由模型在 `Weak Real Zone / Ambiguity Zone` 中二选一。
- `Real Zone` 的 tool 调用失败时，不向上层直接报硬错误，而是退化到 `Weak Real Zone` 或 `Ambiguity Zone`。
- Execution 应是统一执行入口，并包含一个有界的 `execution loop`：
  - executor 先拿真实结果或模拟结果。
  - executor 把结果包装成自然场景描述交给上层负责角色扮演的 agent。
  - 上层 agent 只输出自然语言反应，不直接暴露 schema 风格的 tool calling。
  - executor 再根据该自然语言反应决定是否继续执行下一轮。
- MVP 阶段 `execution loop` 的终止条件至少包括：
  - 上层 agent 的自然语言反应中不再出现新的可执行动作，loop 自然终止。
  - 达到最大轮次。
  - 距离下一个分钟级动作的计划开始时间只剩下可配置 buffer 时，停止当前 loop，给执行后 replan 预留时间。
  - 外部中断到达（例如交互层收到消息）。
- MVP 边界：
  - `Real Zone` 可以在 loop 中把上层 agent 的后续自然语言反应继续转成 follow-up 的真实 tool 调用。
  - `Weak Real Zone / Ambiguity Zone` 在 loop 中继续走模拟/叙事补全，不要求因为后续自然语言里出现了可执行动作就自动升级成新的真实 tool 调用。
- 执行结果必须统一产出最小结构壳，至少包含：
  - `status`
  - `source`
  - `content`
  - `raw_data`

Source:

- `### 2. 生活运行层(运行层)` 中 `Execution`、`Execution loop` 与 `ExecutionResult`，`README.md` lines `161-323`

### Snapshot 契约

- 不能只保留 `Raw Log`。
- 还需要保存关键状态快照，用于回答“系统当时处于什么状态”。

Source:

- `### 补充：状态快照（Snapshot）`，`README.md` lines `319-337`

### 运行层调试 MVP

运行层调试优先围绕可观察性与可控性展开，先不扩成完整运维后台。

MVP 需要覆盖的 9 项能力：

- `状态查看`
  - 目标：随时看到 runtime 现在在做什么。
  - 至少包括：`runtime_status`、scheduler 是否在跑、当前 action、下一步 step、下一次唤醒时间、最近一次 outcome、最近一次 error。
- `单步推进`
  - 目标：手动让系统只推进一轮，观察“如果现在立刻运行，会发生什么”。
- `暂停与恢复`
  - 目标：临时停掉后台自动推进，再恢复到正常调度状态。
- `时间控制`
  - 目标：人工设置当前时间或快进一段时间，用来调试分钟级动作、跨小时、跨天和 day-start。
- `当前计划查看`
  - 目标：查看当前 day/hour/minute plan 以及 active step，便于判断 runtime 为什么这样推进。
- `最近执行结果查看`
  - 目标：查看最近一次执行做了什么、产出了什么结果、为什么停止。
- `最近 replan 决策查看`
  - 目标：查看最近一次是否 replan、为什么 replan、属于哪一类 replan。
- `工具与 MCP 状态查看`
  - 目标：查看当前注册的 tools、来源类型，以及 MCP server 的连接和注册状态。
- `最近错误查看`
  - 目标：快速定位最近一次失败发生在哪个阶段、停在什么地方。

当前明确不做：

- `事件队列查看`
  - 先不进入 MVP 调试面，避免把第一版 UI 和接口扩成更重的运维视图。

### Memory 契约

记忆至少分四层：

- `Raw Log`
- `Core Memory`
- `Active Memory`
- `Archive Memory`

MVP 要求：

- `Raw Log` 是 append-only 原始记录。
- `Core Memory` 是关键 LLM 调用的常驻上下文。
- `Active Memory` 是近期可检索记忆。
- `Archive Memory` 在 MVP 阶段可以延后，但边界必须明确。

Source:

- `### 四层记忆架构`，`README.md` lines `396-466`

### 数据流契约

推荐数据流：

`交互层或运行层产生记录 -> 归一化为标准事件 Raw Log -> 记忆提取与加工 -> Active Memory -> 定期压缩到 Archive Memory -> 关键变化回写 Core Memory`

MVP 可以延后 Archive/Core 的部分压缩与回写，但主链路方向不能偏。

Source:

- `### 记忆数据流`，`README.md` lines `467-476`

### 模型与框架契约

- `PydanticAI` 只作为局部能力层，不作为系统总控框架。
- `runtime_orchestrator` 仍然由项目自己实现。
- 业务层不应直接绑死到某个框架内部类型。
- 至少区分 `dialogue / decision / memory` 三类模型路由。

Source:

- `### PydanticAI 的使用原则`，`README.md` lines `535-557`
- `### 多模型与多 API 路由`，`README.md` lines `558-582`

## 技术栈范围

README 明确过的 MVP 技术栈：

- `Python 3.12`
- `uv`
- `asyncio`
- `FastAPI`
- `Pydantic`
- `SQLAlchemy + SQLite`
- `JSONL Raw Log`
- `PydanticAI`
- 官方模型 SDK
- 官方 MCP Python SDK
- `pytest`
- `Ruff`

Source:

- `## MVP 技术栈`，`README.md` lines `503-534`

## 目录结构解释

README 给出的目录结构是“职责表达”，不是必须逐字照抄。

当前应优先满足：

- 目录表达模块边界
- 文件表达职责
- 模块长大后再拆子目录

Source:

- `## 建议目录结构`，`README.md` lines `583-638`

## 建议读法

日常开发的推荐顺序：

1. 先看本文件的 `MVP硬边界`、`运行与规划约束`、`模块映射`。
2. 再看 `task_plan.md`，确认当前真正要推进的阶段。
3. 如果某个约束需要回溯原文，沿着本文件的 README 来源回到对应章节。
4. 当前做什么、做到哪一步、还剩什么，不写进本文件，写进 planning files。
5. 如果 README 更新了边界或约束，先同步本文件，再继续写代码。
