# Memory Layer

`app/memory` 负责 Amadeus 的记忆层实现。

这一层的目标不是替代 runtime 的完整状态，而是提供一套更稳定的记忆接口，让 planning、execution、replan、interaction 这些运行时模块可以用统一方式读写：

- 核心记忆
- 活跃记忆
- 归档记忆
- 原始日志
- 运行时快照

## 这一层提供什么

### 1. `models.py`

定义记忆层的核心数据对象：

- `CoreMemory`
  - 当前 persona 的核心记忆
  - 当前包含：
    - `core_date`
    - `soul_md`
    - `today_plan_lines`
    - `today_execution_records`
    - `recent_events`
    - `updated_at`
- `CoreMemoryExecutionRecord`
  - 核心记忆里的单条执行记录
- `ActiveMemoryEntry`
  - 活跃记忆条目
  - 支持 embedding、importance、interaction_partner 等字段
- `ArchiveMemoryEntry`
  - 归档记忆条目
- `RawLogEntry`
  - 原始日志
  - 用来记录 event、planning、outcome、model_io、replan 等底层信息
- `RuntimeSnapshot`
  - runtime state 的快照

### 2. `service.py`

这是这一层的主入口，也是正常情况下唯一应该直接被 runtime 调用的对象。

核心职责：

- 加载和持久化各类记忆文件
- 维护 `self.core_memory`
- 接收 execution / planning / replan 产生的结果并写回记忆
- 提供检索上下文给 planning / execution / replan / interaction
- 负责 active memory 归档滚动
- 负责 runtime snapshot 落盘

常用接口：

- `update_persona_context()`
  - 更新 `soul_md`
- `reset_core_memory()`
  - 重置核心记忆
- `update_plan_context()`
  - 用 `day_blocks` 更新 `today_plan_lines`
- `record_outcome()`
  - 写入一条执行结果
  - 同时更新：
    - `today_execution_records`
    - `recent_events`
    - `active_memory.jsonl`
    - `raw_log`
- `record_runtime_event()`
  - 记录运行时事件到 raw log
- `record_planning_trace()`
  - 记录 planning 调试信息
- `record_model_trace()`
  - 记录模型 I/O
- `record_replan_decision()`
  - 记录 replan 决策
- `save_snapshot()`
  - 保存 runtime state 快照
- `day_start_memory_context()`
  - 给 day start planning 提供记忆上下文
- `replan_memory_context()`
  - 给 replan / ambiguity 等流程提供记忆上下文
- `interaction_memory_context()`
  - 给对话流程提供相关记忆
- `core_prompt_context()`
  - 把核心记忆整理成 prompt 里能直接使用的自然语言文本

### 3. `retrieval.py`

负责记忆检索与排序。

当前检索管线由 `MemoryRetrievalPipeline` 提供，支持：

- semantic hit
- bm25 hit
- emotional hit
- rerank

对外重要对象：

- `MemoryRetrievalPipeline`
- `MemoryCandidate`
- `MemoryReranker`
- `EmbeddingGenerator`
- `rank_memory_entries()`

这一层本身不关心 planning/replan 的业务逻辑，只负责“给定 query，如何从 entry 里选出更相关的记忆”。

### 4. `snapshots.py`

提供 `make_snapshot()`，把 `RuntimeState` 转成 `RuntimeSnapshot`。

## 记忆文件长什么样

默认情况下，`MemoryService` 会维护这些文件：

- `memory/core_memory.json`
- `memory/active_memory.jsonl`
- `memory/archive_memory.jsonl`
- `memory/snapshots.jsonl`
- `memory/raw_log/<date>/entries.jsonl`

在多 persona 模式下，这些文件不会共用一套全局路径，而是每个 persona workspace 各自维护一套。

## 核心记忆和 runtime state 的关系

要特别区分下面两件事：

- `RuntimeState.plan`
  - 这是 runtime 当前正在执行的完整计划状态
  - 里面有 `day_blocks`、`minute_steps`、active block 等完整结构
- `CoreMemory`
  - 这是记忆层保存的“给后续推理使用的稳定上下文”
  - 它不要求保存 runtime 的所有细节

当前设计里：

- 完整日计划保存在 `RuntimeState.plan`
- 核心记忆里保存的是自然语言版的 `today_plan_lines`
  - 例如：
    - `07:00-08:00: 起床洗漱、早餐`
    - `08:00-12:00: 上课`

注意：

- `core_memory.json` 这个文件本身仍然是 JSON
- 但 `today_plan_lines` 的内容已经是自然语言
- `core_prompt_context()` 取出来喂给模型时，会把这些行拼成纯文本，而不是把 `day_blocks` 的对象结构直接塞进 prompt

## 典型调用链

### 1. 计划生成后

- `planning` 生成 `PlanState`
- `orchestrator` 更新 `state.plan`
- `orchestrator` 调用 `memory_service.update_plan_context(day_blocks=plan.day_blocks, ...)`
- `MemoryService` 把 `day_blocks` 转成 `today_plan_lines`

### 2. 执行完成后

- `execution` 产出 `ActionOutcome`
- `orchestrator` 调用 `memory_service.summarize_outcome()`
- `orchestrator` 调用 `memory_service.record_outcome()`
- `MemoryService`：
  - 写 raw log
  - 写 active memory
  - 更新 `today_execution_records`
  - 更新 `recent_events`
  - 必要时触发 active -> archive 滚动

### 3. replan 决策时

- `replan` 通过 `memory_service.core_prompt_context()` 获取核心上下文
- 再通过 `memory_service.replan_memory_context()` 获取检索补充记忆
- 两部分一起作为模型输入的一部分

## 当前推荐的使用方式

推荐：

- 把 `MemoryService` 当成这一层的唯一公开入口
- 让 runtime 模块通过 service 方法更新记忆
- 让 prompt 构造逻辑通过 `core_prompt_context()` / `*_memory_context()` 取上下文

不推荐：

- 在 runtime 外部直接改 `memory_service.core_memory.xxx`
- 改完以后再手动调 `_touch_core_memory()`

现在仓库里仍然有少量调试入口会这么做，这属于临时便捷路径，不建议继续扩散。

## 当前边界

这一层负责：

- 记忆对象定义
- 记忆持久化
- 记忆检索
- prompt 可消费的记忆上下文构造

这一层不负责：

- 决定今天该怎么规划
- 决定是否 replan
- 执行具体 step
- 对外发消息

这些逻辑仍然属于 runtime / communication / persona 等模块。

## 如果后面要继续演进

几个自然的演进方向：

- 把核心记忆进一步拆成更明确的自然语言段落结构
- 减少外部直接操作 `core_memory` 的地方，统一走 service 接口
- 把“执行结果何时压缩、如何压缩”从当前实现里抽成更明确的策略
- 让 memory layer 对 persona workspace 的约束更显式
- 给这一层补更系统的单元测试和集成测试
