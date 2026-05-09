# Findings & Decisions

## Requirements
- `RoleplayAgentContext` should become the day-scoped context source of truth.
- It should be persisted separately from `CoreMemory`, in `roleplay_context.json`.
- `CoreMemory` should now only carry stable persona-level information, with `soul_md` as the only active field for now.
- Execution, interaction, and retrieval should write natural-language blocks into the persisted roleplay context.
- Planning should eventually use yesterday’s finished roleplay context for planning-only retrieval, but that planning-query shaping can be simple for now.

## Research Findings
- `MemoryService` now owns a dedicated `roleplay_context.json` store and persists a real `RoleplayAgentContext`.
- `MemoryService.build_roleplay_agent_context()` now starts from the persisted context, then refreshes:
  - `soul_md` from `CoreMemory`
  - `plan_context` from current `state.plan.day_blocks`
- `ExecutionService` and `InteractionService` now both persist their context mutations back into `roleplay_context.json`.
- `RoleplayAgentContext` now carries:
  - `context_date`
  - today’s `entries`
  - `previous_context_date`
  - `previous_entries`
- Day-start planning now rotates the roleplay context:
  - yesterday’s `entries` move to `previous_entries`
  - today starts from empty running `entries`
- `MemoryService.day_start_memory_context()` now prefers `previous_entries` before falling back to active/archive memory.
- Active/archive memory retrieval is already functional and should remain intact:
  - retrieval still depends on `active_memory.jsonl` / `archive_memory.jsonl`
  - interaction retrieval already uses `interaction_partner` priority
- `JsonFileStore` already exists and is sufficient for `roleplay_context.json`.
- `PersonaWorkspace` currently exposes:
  - `core_memory.json`
  - `active_memory.jsonl`
  - `archive_memory.jsonl`
  - `snapshots.jsonl`
  - `raw_log/`
  - but not yet `roleplay_context.json`
- `CoreMemory` has already been narrowed to:
  - `soul_md`
  - `stable_facts`
  - `relationship_conclusions`
  - `important_conclusions`
  - `updated_at`
- The temporary compatibility layer for removed core-memory day fields has already been fully removed.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Persist `RoleplayAgentContext` via `JsonFileStore` | The object is naturally JSON-serializable and should survive restarts |
| Keep active/archive memory as the retrieval substrate | Day context and retrievable memory serve different purposes |
| Keep `RoleplayAgentContext.entries` block-based with `kind/content/metadata` | This matches the intended “living stream” semantics and is easy to re-render |
| Let `build_roleplay_agent_context()` start from persisted context, then refresh `soul_md` and current plan text | Preserves day continuity without freezing prompt rendering |
| Defer sophisticated planning-query synthesis | The user prefers to validate behavior first and iterate later |
| Keep retrieved-memory blocks rendered as natural language (`你想起了一些事情：`) while preserving `kind` internally | Keeps immersion without losing debuggability |

## Open Questions
| Question | Current Lean |
|----------|--------------|
| How exactly should day-boundary reset happen? | Landed as explicit day-start rotation keyed by date |
| Should `roleplay_context.json` also store the date it belongs to? | Yes; `context_date` and `previous_context_date` are now part of the persisted structure |

## Latest Adjustment
- `ReplanService` input semantics were simplified:
  - keep `now`, `state`, `event`, `outcome`, and retrieved memory context
  - remove `plan_exhausted` from the service API and prompt
  - remove prompt-level dependence on `outcome.status`
- Planner-lab and main plan-lab debug endpoints were aligned to this minimal shape.

## New Task: Proactive Interaction
- The next runtime feature is proactive outbound interaction initiated from execution.
- Trigger rule:
  - if the roleplay side clearly wants to proactively contact someone, execution should stop with the new reason `proactive_interaction`
  - the handoff payload is minimal: `name`, `message_content`
- The tuned existing executor prompt must stay untouched; any extra detection prompt must be added separately.
- Targets should come from a simple registered contact book rather than an open-ended freeform target space.
- Outbound proactive interaction should reuse the interaction mainline idea, but with the first message authored by the roleplay agent rather than the user.

## Proactive Interaction Findings
- Added a simple runtime `ContactBook` plus an internal `list_contacts` tool.
- Inbound interaction now registers the current user into the contact book (`name`, `recipient_id`, `channel`).
- Execution now uses a separate proactive-interaction detector prompt:
  - it does not modify the tuned existing executor prompt
  - it can call `list_contacts`
  - it returns only `name` + `message_content`
- Execution loop now supports the new stop reason `proactive_interaction`.
- `RuntimeOrchestrator` now detects that stop reason after execution and immediately hands off to outbound interaction before replan.
- `InteractionService` now has two paths:
  - inbound `execute_interaction(...)`
  - outbound `execute_outbound_interaction(...)`
- Outbound interaction writes a natural-language block like:
  - `你打开了和{partner}的聊天窗口。`
  - `【渠道】`
  - `{角色名}: {message}`

## Resources
- `app/memory/service.py`
- `app/runtime/roleplay_context.py`
- `app/runtime/execution.py`
- `app/runtime/interaction.py`
- `app/runtime/planning.py`
- `app/persona/registry.py`
- `app/infra/storage.py`
- `tests/test_memory.py`
- `tests/test_execution_memory_injection.py`
- `tests/test_interaction.py`

## New Task: Standalone Executor-Lab Alignment
- The standalone execution double-loop debug page should mirror the current execution runtime more closely.
- The most important mismatches are:
  - proactive interaction handoff is not surfaced in the page
  - the standalone contact roster behind `list_contacts` is empty and not user-configurable
  - the page still carries stale labels and legacy zone semantics from the older runtime
- The standalone lab should remain execution-focused:
  - manual `RoleplayAgentContext` input is still desired
  - planning/replan full-chain state is still out of scope
  - but the lab must be able to debug the current execution handoff behavior honestly

## Latest Adjustment: Proactive Detection Ownership
- The previous proactive-interaction implementation used a second dedicated detector agent after each roleplay response.
- That was semantically off for this project:
  - proactive handoff judgment should still belong to the executor itself
  - not to a second auxiliary agent
- The corrected shape is:
  - keep the tuned executor prompt body untouched
  - append a small proactive-output rule to the existing executor prompt
  - let the executor return optional `name` + `message_content` together with `scene/result/stop`
  - remove the extra detector-agent pass entirely

## Latest Adjustment: Full Executor History
- The executor previously only saw:
  - the previous round's `scene/result`
  - the latest roleplay reply
  - core-context text
- That was too shallow for the intended double-loop semantics.
- The executor now receives a rendered `executor_history` block that includes all prior rounds' relevant content:
  - each roleplay reply
  - each executor-side tool call summary
  - each executor output's `scene/result/stop`
  - proactive handoff payload (`name`, `message_content`) when present

## New Task: Interaction Cooldown Termination
- The user wants interaction termination to be time-based rather than model-judged.
- Desired behavior:
  - every inbound user message triggers one roleplay reply
  - after sending that reply, the role enters a cooldown window
  - if a new message arrives during cooldown, interaction continues and the cooldown resets
  - if the cooldown expires with no new message, the runtime should run one normal `replan`
- The same cooldown rule should also apply after outbound proactive interaction.
- Default cooldown length should be `3` minutes, but it must be configurable.
- Implementation should stay minimal:
  - avoid introducing a separate interaction-session entity unless absolutely necessary
  - reuse the normal `replan` flow instead of inventing a special post-interaction replan path

## Interaction Cooldown Findings
- `InteractionService` currently handles:
  - inbound `execute_interaction(...)`
  - outbound `execute_outbound_interaction(...)`
  - but does not manage any waiting/cooldown state
- `RuntimeOrchestrator` already owns:
  - event dispatch
  - wake scheduling via `next_wake_at()`
  - post-execution replan decisions
  - so it is the most natural place to absorb cooldown timeout handling
- `RuntimeState` currently has no interaction-specific fields, so some minimal runtime state extension will be required.
- The existing runtime already knows how to surface future wake times, which means cooldown expiry can likely piggyback on the same scheduler-facing wake mechanism instead of needing a new standalone scheduler.

## Interaction Cooldown Implementation Findings
- The leanest workable runtime shape still needed a small amount of persisted cooldown state:
  - `interaction_cooldown_until`
  - `interaction_cooldown_context`
  - `interaction_cooldown_resume_after_completion`
- A single deadline was not enough:
  - timeout-triggered `replan` still needs some outcome-like context
  - proactive outbound interaction also needs to remember whether the underlying execution step had already exhausted the current plan
- The cleanest scheduling behavior is:
  - pending events still win first
  - day-start planning still wins before cooldown timeout if the date rolled over
  - otherwise an active cooldown blocks due execution steps/blocks until expiry
- Cooldown expiry can reuse `EventType.SCHEDULE_WAKE` with a timer-source payload:
  - no new event entity was necessary
  - the timeout reason is carried in `payload["reason"] == "interaction_cooldown_expired"`
- Inbound interaction no longer runs immediate replan decision/apply logic.
- Proactive outbound interaction also no longer re-enters the execution/replan path immediately; it first opens the same waiting window as inbound chat.

## New Task: Integrated Frontend Workspace
- The next task is to replace the current product-facing entry page with a real integrated frontend.
- Desired navigation shape:
  - `角色`
  - `工作台`
  - `聊天`
  - `设置`
- Desired section responsibilities:
  - `角色`: create/delete/activate personas and edit `soul.md`
  - `工作台`: show active persona, runtime state, virtual time controls, day plan, and per-plan execution details
  - `聊天`: show inbound/outbound messages and allow sending user messages to the active role
  - `设置`: show tool status / MCP status
- Debug information should not live on a separate product page; it should stay folded by default inside the relevant UI sections.

## Integrated Frontend Findings
- The current product-facing front entry in `app/main.py` still serves the standalone executor-lab page.
- The main reusable frontend assets today are:
  - `app/front/pages/executor-lab-standalone.html`
  - `app/front/assets/executor-lab-standalone.js`
  - `app/front/pages/planner-lab-standalone.html`
  - `app/front/assets/planner-lab-standalone.js`
- The existing API surface already covers most of the first integrated frontend:
  - persona CRUD / activation / soul update
  - message sending
  - runtime state
  - runtime debug
  - runtime clock controls
  - memory inspection
  - tool debug
- There is no dedicated product-facing conversation-feed endpoint yet.
- `CommunicationHub` is only an in-memory outbox drain for immediate responses, not a persistent chat history store.
- The most practical first chat-feed source is therefore a derived view from persisted interaction-related runtime/memory data rather than a new conversation entity.

## Integrated Frontend Implementation Findings
- The cleanest integrated frontend shape was:
  - a new `workspace.html` / `workspace.css` / `workspace.js`
  - keep `/front/debug` and `/front/executor-lab` intact for engineering use
  - switch `/` and `/front/workspace` to the new integrated page
- Two thin aggregated APIs were enough to keep the frontend simple without inventing new domain entities:
  - `/api/workspace/workbench`
  - `/api/workspace/chat`
- The workbench endpoint now packages:
  - runtime summary
  - raw runtime state
  - current plan
  - latest execution / latest replan
  - plan items with attached execution records
- The chat endpoint now packages:
  - a derived message feed from persisted `RoleplayAgentContext.entries`
  - roleplay-context preview for folded debugging
- The first integrated workbench uses polling rather than a new product-facing stream endpoint.
- The first integrated chat view is intentionally single-stream:
  - no multi-thread conversation list
  - no separate conversation entity
  - just the current persisted interaction flow around the active role
