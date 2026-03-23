# Amadeus

## 项目定位

Amadeus 是一个面向“可持续生活的虚拟人格”的 agent 项目。

它不是单轮聊天机器人，而是一个可以持续运行的人格驱动行为系统：基于用户设定的人设长期存在，按天生成计划，按分钟推进动作，通过消息渠道与外界互动，必要时调用 MCP 做真实操作，并在执行结果或外界介入后持续 replan。

一句话概括：

`输入人设描述 -> 输出符合该人设的连续生活轨迹与互动行为`

这个引擎既可以承载虚构角色，也可以承载真实人的数字分身。底层是同一个 runtime，只是输入的人设和场景不同。

## 学术与产品方向

这个项目可以理解为对 Generative Agents 路线的产品化落地：

- 从封闭沙盒走向真实互联网
- 从多 agent 社会模拟收束到单 agent 持续生活
- 从“会动的角色”推进到“能长期保持身份一致、行为连续、与真实世界交互的角色”

对标关系可以粗略表述为：

- task-driven agent runtime 解决“任务怎么完成”
- character-driven agent runtime 解决“这个人怎么持续地活”

## 当前现状

截至 `2026-03-23`，`Amadeus` 已经从纯设计阶段进入 MVP 骨架实现阶段。

当前已经落地的最小能力包括：

- FastAPI 应用入口与基础 runtime orchestrator
- `POST /api/messages` 单轮闭环
- 原生 QQ gateway adapter MVP
- 本地启动 / 停止 / 状态 / 隧道脚本
- `Raw / Core / Active` 的基础持久化
- runtime state 恢复与检查接口

当前工作区同级目录中已有可复用资料：

- `qq-codex连接服务`
  - 可作为消息渠道接入和桥接层参考
- `小手机相关`
  - 可参考移动端消息交互、主动触达和行为模拟
- `论文以及相关资料`
  - 可参考分层规划、记忆、反思、模拟循环等设计

结论：代码可以从零开始，但信息和参考材料并不是从零开始。

## 设计原则

当前已经确认的高层原则：

- 先支持 `1 个角色`
- 先支持 `1 个真实用户`
- 先接入 `1 个消息渠道`
- 先做 `滚动规划`
- 先做 `执行后 replan`
- 先做 `MCP 兼容层`
- 先做完整 `Raw / Core / Active / Archive` 记忆架构
- 先保证自然 fallback，不追求第一版就覆盖全部复杂能力
- 系统优先保证：`连续`、`自洽`、`可回溯`、`可扩展`

## 四大层架构

Amadeus 当前最适合按四个大层来理解，而不是按 service 平铺。

### 1. 角色层

负责回答：`这个人是谁`

关注长期稳定的身份一致性，而不是短期行动安排。

包含内容：

- `persona_service`
  - 接收用户初始人设文案
  - 通过 AI 追问补全设定
  - 输出结构化角色档案
- 人设摘要
- 长期关系设定
- 角色稳定特质与长期偏好

这一层解决的是人格稳定性，不直接决定今天几点做什么。

### 2. 生活运行层

负责回答：`这个人今天怎么活`

这是系统心脏，负责把角色从设定推进为持续运转的生活过程。

包含内容：

- `planning_service`
  - 生成今日摘要计划
  - 每小时细化小时计划
  - 每次只展开未来 `5-15 分钟` 的分钟级动作
- `execution_service`
  - 执行分钟级动作
  - 支持 `tool / hybrid / narrative` 三种执行模式
  - 执行结束后产出标准化 outcome
- `emotion_engine`
  - 维护当前情绪状态
  - 采用 `文本 + VAD` 双重表示
- `replan_engine`
  - 在执行结果、消息介入、情绪变化后决定是否 replan
  - MVP 先支持 `no_replan / micro_replan / hour_replan`
- `interaction_policy`
  - 决定是否立即回复、延迟回复、主动发起话题、只写入记忆等行为策略

这一层负责产出“经历”和“行为结果”，也就是系统最原始的一手运行数据。

### 3. 交互层

负责回答：`这个人怎么和外界接触`

重点不是聊天本身，而是把外部世界的介入转成系统内部可处理的运行时事件。

包含内容：

- `communication_hub`
  - 统一通信层，而不是单纯对话层
  - 负责 `user -> agent`
  - 负责 `agent -> user`
  - 后续支持 `agent -> agent`
- `channels`
  - 具体渠道适配器，例如 QQ、Telegram、Web 等

这一层把用户消息、渠道事件、主动外发消息统一成系统可消费的 event。

### 4. 基础设施层

负责回答：`系统怎么稳定落地`

它不定义角色行为，但决定系统是否可运行、可扩展、可回放、可维护。

包含内容：

- `runtime_orchestrator`
  - 系统总调度器
  - 负责时钟、事件队列、计划推进、模块编排
- `mcp_compat_layer`
  - 统一能力注册、动作映射、结果归一化、错误归类、fallback 建议
- `storage`
  - 持久化、索引、快照、检索底层
- `logging`
  - 系统日志、审计日志、评估日志
- `models`
  - 模型路由、参数管理、调用封装

这里尤其要强调：`runtime_orchestrator` 虽然归在基础设施层，但它是整个系统的控制中枢。

## 横切子系统：记忆系统

记忆系统不适合被理解成“普通存储层”，它更像一个横切角色层、生活运行层和基础设施层的状态系统。

边界定义如下：

- `生活运行层` 负责产出经历
- `记忆系统` 负责把经历变成可追溯、可检索、可回忆、可压缩的记忆

一句话概括：

`运行层负责发生什么，记忆系统负责怎么记住`

### 四层记忆架构

#### 1. 原始记忆（Raw Log）

- 完整未加工数据
- 内容包括：对话原文、MCP 返回原文、动作执行日志、replan 判断、情绪更新结果、关键内部事件
- append-only，按天存储
- 默认不参与检索

作用：

- 追溯系统当时经历了什么
- 作为记忆重建和评估的数据源
- 支持 debug、审计、replay、论文分析

#### 2. 核心记忆（Core Memory）

- 常驻 prompt，每次关键 LLM 调用都会携带
- 不做检索，由事件驱动更新

内容包括：

- 人设摘要
- 关系状态
- 当前情绪
- 今日计划摘要

它相当于：

`我是谁，我现在怎么样`

#### 3. 活跃记忆（Active Memory）

- 最近 `7-14 天` 的加工后记忆条目
- 每条记录包含：
  - 内容
  - 时间戳
  - 类型
  - importance score
  - 语义 embedding
  - 情感 embedding

能力目标：

- 语义向量检索
- BM25 检索
- 情感向量检索
- reranker 精排

它相当于：

`最近发生了什么`

#### 4. 归档记忆（Archive Memory）

- 活跃记忆过期后的压缩摘要
- 保留关键事件和高 importance 条目
- 平时不参与常规检索
- 活跃记忆召回不足时作为回退层
- 可通过时间戳回指 Raw Log 重建细节

它相当于：

`以前发生过什么`

当前实现进度：

- `Raw / Core / Active` 已有基础落盘能力
- `Archive` 正在补齐最小可用归档与检索路径

### 补充：状态快照（Snapshot）

仅保存 Raw Log 适合追溯“发生了什么”，但不一定能完整还原“系统当时处于什么状态”。

因此建议同时保存关键状态快照，例如：

- 当前 Core Memory
- 当前计划状态
- 当前情绪状态
- 当前运行时队列或任务状态

可以理解为：

- `Raw Log = 事实流`
- `Snapshot = 状态面`

两者结合，才能更完整地回溯系统运行过程。

### 记忆数据流

建议采用以下流向：

`外部输入 / 定时器 / MCP 结果 -> 交互层或运行层归一化为标准事件 -> Raw Log -> 记忆提取与加工 -> Active Memory -> 定期压缩到 Archive Memory -> 关键变化回写 Core Memory`

## 运行主循环

Amadeus 的运行核心不是单个 service，而是 `runtime_orchestrator` 驱动的事件流和状态流。

核心主链路如下：

1. `交互层` 或 `定时器` 产生事件
2. `runtime_orchestrator` 接收并分发事件
3. `interaction_policy` 判断是否需要立即响应、延迟响应、仅记忆、不打断当前动作
4. `planning_service` 生成或调整计划
5. `execution_service` 执行动作并选择执行模式
6. `mcp_compat_layer` 在需要时调用真实能力
7. 产出统一 `outcome`
8. `emotion_engine` 更新情绪
9. `记忆系统` 写入 Raw Log、更新 Active/Core，必要时写入快照
10. `replan_engine` 判断是否调整后续计划
11. `communication_hub` 决定是否向外发送消息

### MVP 运行驱动说明

在当前 README 约定下，MVP 是 `事件驱动` 的，而不是 `heartbeat 驱动` 的。

- 用户消息属于外部事件，可随时中断当前计划
- 定时器的职责是：
  - 在下一个计划动作时间点唤醒 runtime
  - 在下一个小时规划边界触发新的小时计划
- 分钟级 plan 是实际执行单元
- 动作执行完成后，才决定是否 replan
- 当前实现中，`replan` 会在两种情况下真正刷新下一段短计划：
  - 上一个动作结果退化或失败
  - 当前短计划窗口已经执行完
- MVP 暂不实现“空闲时每隔几分钟自己想点什么做”的 heartbeat 兜底机制

换句话说：

- 有明确分钟级计划时：由计划时间表推进
- 无明确动作时：系统可以保持安静，直到下一个计划节点或外部事件

## 规划策略

MVP 明确采用分层规划，但不做全天分钟级一次性铺满。

当前策略：

- `00:00` 或首次启动时生成“今日摘要计划”
- 每小时开始时生成“小时计划”
- 每次只展开未来 `5-15 分钟` 的动作

执行语义：

- 小时计划用于限定当前时间窗的方向
- 分钟级动作用于真实执行与调度
- 调度器按下一个分钟级动作的计划时间点唤醒
- 不采用固定间隔 heartbeat 轮询来推进整条生命流

这样做的好处：

- 减少长期计划漂移
- 降低成本
- 更贴近真实人的行为节奏
- 更容易在用户介入后自然 replan

## MCP 兼容层职责

`mcp_compat_layer` 是基础设施层里的关键能力层，不是附属工具集合。

MVP 至少要做到：

- `Capability Registry`
  - 当前系统有哪些能力可用
- `Action Resolution`
  - 把高层动作映射到具体 MCP 工具
- `Unified Action Schema`
  - 上层发统一动作请求
- `Unified Outcome Schema`
  - 下层不同 MCP 返回统一结果
- `Failure Normalization`
  - 错误至少归类为：
  - `success`
  - `partial_success`
  - `retryable_failure`
  - `blocked_failure`
- `Fallback Hook`
  - MCP 失败时给出重试、换路由、叙事退化建议

当前代码里的 MVP 外部信息能力目前有两个：

- 当用户消息里包含 URL 时，`planning_service` 会生成一个 `tool` step
- `execution_service` 会通过 `mcp_compat_layer` 调用 `read_url`
- `read_url` 负责抓取页面、提取可读文本，并返回统一的 `outcome`
- 当用户消息显式表达搜索意图时，`planning_service` 会生成 `search_web` 的 `tool` step
- 当前 `search_web` 先通过 DuckDuckGo Instant Answer 形态返回结构化搜索结果

原则上，业务层不应直接绑定具体 MCP server。

## MVP 边界

第一阶段先收敛到以下范围：

- `1 个角色`
- `1 个真实用户`
- `1 个消息渠道`
- `1 套滚动规划`
- `1 个最小可用 MCP 能力`
- `1 套完整运行闭环`

这个闭环至少包括：

- 人设初始化
- 事件接入
- 日计划与小时计划
- 分钟级动作执行
- 执行后 replan
- 记忆写入
- 情绪更新
- 对外消息回复或主动触达

当前按 README 收敛 MVP 时，可优先保证：

- agent 自己的生活主链路能跑起来
- 用户能在任意时刻介入并影响后续计划

`主动触达` 仍然属于系统目标方向，但不是当前文档里最清晰的一期硬门槛。

## MVP 技术栈

当前确认的 MVP 技术栈如下：

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

各组件的职责大致如下：

- `Python 3.12 + asyncio`
  - 作为主运行时和单进程 daemon 基础
- `FastAPI`
  - 提供最小 HTTP API、Webhook、调试入口和后续 Web 渠道入口
- `Pydantic`
  - 定义 `Event / State / Outcome / MemoryEntry` 等核心 schema
- `SQLAlchemy + SQLite`
  - 存储结构化状态、计划、索引、核心记忆元数据
- `JSONL Raw Log`
  - 按天保存 append-only 原始事件流与执行记录
- `pytest + Ruff`
  - 分别负责测试与代码质量

### PydanticAI 的使用原则

当前决定使用 `PydanticAI`，但只把它作为局部能力层，不作为系统总控框架。

也就是说：

- `runtime_orchestrator` 仍然由项目自己实现
- 系统内部标准类型仍然使用自己的 `core/*` schema
- `PydanticAI` 主要用于：
  - 结构化输出
  - 局部 tool call
  - 计划生成
  - 记忆提炼
  - replan 判断

为了便于未来切换框架，必须做好隔离：

- 业务层不直接依赖 `PydanticAI` 的内部类型
- 框架相关实现尽量收敛在 `infra/model_client.py` 等适配层
- `runtime`、`memory`、`persona` 等模块只依赖项目自己的输入输出接口

原则上，未来如果需要替换为别的 agent 框架或直接手写 SDK，改动应主要集中在适配层，而不是波及整个系统。

### 多模型与多 API 路由

MVP 需要从第一版开始支持按工作负载拆分模型配置，而不是默认所有场景共用同一个 API。

至少区分三类角色：

- `dialogue`
  - 面向用户对话
  - 优先表达质量和人格一致性
- `decision`
  - 用于 replan 判断、执行分支选择、策略决策
  - 可以优先考虑成本
- `memory`
  - 用于记忆提炼、压缩、分类、整理
  - 可以优先考虑成本和吞吐

因此系统配置层必须支持：

- 不同角色使用不同 provider
- 不同角色使用不同 model
- 不同角色使用不同 API key 或 base URL

实现上，这层能力应通过项目自己的路由与适配层暴露，而不是把业务代码直接绑定到某个框架或某一个模型供应商。

配置示例见：

- [.env.example](c:/Users/lenovo/Desktop/agent/Amadeus/.env.example)
- [docs/configuration.md](c:/Users/lenovo/Desktop/agent/Amadeus/docs/configuration.md)

当前调试接口还包括：

- `POST /api/persona/bootstrap`
- `GET /api/persona`
- `GET /api/runtime/state`
- `GET /api/memory`
- `GET /api/memory/search`

## 建议目录结构

建议从一开始保持简洁：目录表达模块边界，文件表达职责；等某个模块真正长大后，再从单文件拆成子目录。

```text
Amadeus/
  README.md
  docs/
  tests/
  app/
    main.py

    core/
      events.py
      state.py
      outcomes.py
      types.py

    persona/
      service.py
      models.py

    runtime/
      orchestrator.py
      planning.py
      execution.py
      emotion.py
      replan.py
      interaction.py

    communication/
      hub.py
      channels.py

    memory/
      service.py
      models.py
      retrieval.py
      snapshots.py

    mcp/
      compat.py
      registry.py
      schemas.py

    infra/
      storage.py
      model_client.py
      logging.py
```

## 下一步建议

当前已经可以开始落第一版代码，建议顺序如下：

1. 先初始化项目骨架和依赖管理
2. 先定义统一 `Event Schema`
3. 先定义统一 `Outcome Schema`
4. 先定义运行态 `State Schema`
5. 先搭出 `runtime_orchestrator` 最小主循环
6. 再接入 `persona / memory / communication / mcp` 的最小接口

## 当前共识摘要

当前已经确认的关键共识：

- `dialogue_service` 改为 `communication_hub`
- 顶层按 `角色层 / 生活运行层 / 交互层 / 基础设施层` 理解
- `mcp_compat_layer` 必须单独存在
- 执行之后必须进入 `replan_engine`
- 规划采用“日摘要 + 小时细化 + 5-15 分钟滚动展开”
- 情绪采用 `文本 + VAD` 的混合表示
- 记忆采用 `Raw / Core / Active / Archive` 四层结构
- `Raw Log` 和关键 `Snapshot` 都应保存
- 使用 `PydanticAI`，但必须通过适配层隔离
- `dialogue / decision / memory` 必须支持分别配置模型与 API
- MVP 技术栈采用 `Python 3.12 + uv + asyncio + FastAPI + Pydantic + SQLAlchemy/SQLite + JSONL Raw Log + 官方模型 SDK + 官方 MCP Python SDK + pytest + Ruff`
- 系统优先保证“连续、自洽、可回溯、可扩展”
