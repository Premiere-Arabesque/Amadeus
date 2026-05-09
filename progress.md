# Progress Log

## Current Session

### Phase 1: Define Storage + Service Boundaries
- **Status:** completed
- Actions taken:
  - Re-read the planning files and switched them from the previous hour-granularity task to the new roleplay-context persistence task.
  - Inspected:
    - `app/infra/storage.py`
    - `app/memory/service.py`
    - `app/runtime/execution.py`
    - `app/runtime/interaction.py`
    - `app/runtime/roleplay_context.py`
    - `app/persona/registry.py`
    - `tests/test_support.py`
  - Confirmed that:
    - `RoleplayAgentContext` is still only a per-flow in-memory object
    - there is no `roleplay_context.json` path or store yet
    - execution and interaction both already mutate block entries in memory
    - `CoreMemory` has already been narrowed to stable fields only
  - Confirmed that `JsonFileStore` is enough for persisting the day-scoped context object.
- Files inspected:
  - `app/infra/storage.py`
  - `app/memory/service.py`
  - `app/runtime/execution.py`
  - `app/runtime/interaction.py`
  - `app/runtime/roleplay_context.py`
  - `app/persona/registry.py`
  - `tests/test_support.py`
  - `task_plan.md`
  - `findings.md`

### Phase 2: Wire Runtime Reads/Writes
- **Status:** completed
- Actions taken:
  - Rewrote `app/runtime/roleplay_context.py` into a clean UTF-8 block-based context model.
  - Added `roleplay_context_path` to `PersonaWorkspace`.
  - Added a dedicated `roleplay_context.json` store to `MemoryService`.
  - Implemented:
    - `_load_roleplay_context()`
    - `get_persisted_roleplay_agent_context()`
    - `save_roleplay_agent_context()`
    - `reset_roleplay_agent_context()`
  - Switched `build_roleplay_agent_context()` to start from persisted context, then refresh `soul_md` and current plan text.
  - Wired execution to save updated roleplay context after execution/retrieval blocks are appended.
  - Wired interaction to save updated roleplay context after retrieval/incoming-message/reply blocks are appended.
  - Updated persona/main/planner-lab builders so persona-backed workspaces and planner-lab sessions get a dedicated `roleplay_context.json`.
  - Updated in-memory test harnesses to include a roleplay-context store.
  - Normalized retrieved-memory rendering to the natural-language form `你想起了一些事情：`.
- Files changed:
  - `app/runtime/roleplay_context.py`
  - `app/memory/service.py`
  - `app/runtime/execution.py`
  - `app/runtime/interaction.py`
  - `app/persona/registry.py`
  - `app/main.py`
  - `app/planlab_main.py`
  - `tests/test_support.py`
  - `tests/test_memory.py`
  - `tests/test_interaction.py`
  - `tests/test_execution_memory_injection.py`

### Phase 3: Handle Day-Boundary Semantics
- **Status:** completed
- Actions taken:
  - Added `context_date` / `previous_context_date` and `previous_entries` to `RoleplayAgentContext`.
  - Added `rotate_roleplay_agent_context_for_day()` to `MemoryService`.
  - Made `day_start_memory_context()` prefer yesterday’s rotated context entries.
  - Hooked roleplay-context rotation into day-start planning before the planning prompt is built.
  - Added focused tests covering:
    - persisted roleplay-context rotation
    - yesterday-context recall at day start
- Files changed:
  - `app/runtime/roleplay_context.py`
  - `app/memory/service.py`
  - `app/runtime/planning.py`
  - `tests/test_memory.py`

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused runtime suite before this task | `pytest tests/test_memory.py tests/test_interaction.py tests/test_execution_memory_injection.py tests/test_execution_granularity.py -q` | Repo should still be green after narrowing `CoreMemory` | 16 passed | pass |
| Persisted roleplay-context wiring | `pytest tests/test_memory.py tests/test_interaction.py tests/test_execution_memory_injection.py tests/test_execution_granularity.py -q` | New roleplay-context store and runtime writes should stay green | 17 passed | pass |
| Day-boundary rotation | `pytest tests/test_memory.py tests/test_interaction.py tests/test_execution_memory_injection.py tests/test_execution_granularity.py -q` | Rotated roleplay context should preserve yesterday only for planning-side recall | 19 passed | pass |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-04-04 | Historical mojibake and broken strings caused syntax errors while cleaning `CoreMemory` semantics | 1 | Re-encoded affected files as UTF-8 and repaired the broken prompt/context strings before proceeding |

## Next Step
- Do a real end-to-end acceptance pass for:
  - day start planning
  - execution accumulation
  - interaction interruption
  - replan after completion

## Latest Adjustment
- Simplified `replan` inputs and debug forms:
  - removed `plan_exhausted` from the runtime/service/planner-lab replan call chain
  - removed prompt-level `outcome.status` from replan decision inputs
  - kept only the minimal outcome/event/state-driven decision path
- Validation:
  - `py_compile` passed for the touched runtime and debug modules
  - `pytest tests/test_api.py -q -k "plan_lab_endpoints_support_manual_day_start_and_replan or planner_lab_standalone_front_page_is_served or plan_lab_can_boot_in_paused_blank_state"` passed
  - `pytest tests/test_memory.py tests/test_interaction.py tests/test_execution_memory_injection.py tests/test_execution_granularity.py -q` passed (`19 passed`)

## New Session Start: Proactive Interaction
- Switched the planning files to the new task: execution should hand off into interaction when the roleplay output clearly wants to proactively contact a registered target.
- Confirmed from code search that:
  - `ExecutionService` has no proactive handoff yet
  - `InteractionService` currently only supports inbound user-message interaction
  - there is no contact-book tool yet
- Preserved the user constraint:
  - do not modify the tuned existing executor prompt text
  - if needed, add a new prompt/helper separately

## Proactive Interaction Implementation
- **Status:** completed
- Actions taken:
  - Added `app/runtime/contact_book.py` with `ContactBook` / `ContactEntry`.
  - Added internal tool `list_contacts` and wired it through `InternalProvider`.
  - Rewrote `app/runtime/interaction.py` cleanly and added:
    - inbound contact registration
    - `execute_outbound_interaction(...)`
  - Rewrote `app/runtime/roleplay_context.py` cleanly and added outbound interaction context blocks.
  - Added a separate proactive-intent detector in `app/runtime/execution.py`:
    - keeps the tuned existing executor prompt untouched
    - emits `proactive_interaction` + `{name, message_content}`
  - Updated `app/runtime/orchestrator.py` so execution handoff now enters outbound interaction before replan.
  - Updated `app/main.py` so the main runtime shares a single `ContactBook` between internal tools and interaction handling.
  - Added focused tests in `tests/test_proactive_interaction.py`.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Proactive interaction runtime slice | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_proactive_interaction.py tests\\test_interaction.py tests\\test_execution_memory_injection.py tests\\test_execution_granularity.py tests\\test_memory.py -q` | New proactive handoff and existing interaction/execution memory flows stay green | 22 passed | pass |
| Syntax check | `python -m py_compile app\\runtime\\contact_book.py app\\runtime\\roleplay_context.py app\\runtime\\interaction.py app\\runtime\\execution.py app\\runtime\\orchestrator.py app\\tool\\internal_provider.py app\\main.py tests\\test_proactive_interaction.py` | Touched files compile cleanly | passed | pass |

## New Session Start: Standalone Executor-Lab Alignment
- Switched focus to the standalone execution double-loop debug page.
- Confirmed the current mismatch:
  - the standalone lab still manually mirrors the loop
  - proactive interaction handoff is not surfaced there
  - `list_contacts` is wired but uses an empty standalone `ContactBook`
  - the page still has stale/legacy wording and mojibake in many labels

## Standalone Executor-Lab Alignment
- **Status:** completed
- Actions taken:
  - Rewrote `app/front/executor_lab.py` into a cleaner runtime-aligned runner.
  - Added manual contact-book seeding through the standalone page:
    - new roleplay field `registered_contacts`
    - parsed as `name | channel | recipient_id`
  - Wired `front_lab_main.py` to use a shared `ContactBook` with `InternalProvider`, so `list_contacts` in the lab now sees the manually seeded roster.
  - Surfaced proactive handoff in the standalone stream/result flow:
    - `proactive_interaction` event
    - `stop_reason=proactive_interaction`
    - target + first-message payload in the summary UI
  - Cleaned the standalone HTML/JS copy and removed the stale legacy zone values from the page model.
  - Added focused helper tests in `tests/test_executor_lab.py`.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Executor-lab focused runtime slice | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_executor_lab.py tests\\test_proactive_interaction.py tests\\test_interaction.py tests\\test_execution_memory_injection.py tests\\test_execution_granularity.py tests\\test_memory.py -q` | Standalone executor-lab helper changes should not break current runtime semantics | 24 passed | pass |
| Syntax check | `.\\.venv\\Scripts\\python.exe -m py_compile app\\front\\executor_lab.py app\\front_lab_main.py app\\runtime\\contact_book.py tests\\test_executor_lab.py` | Touched backend files compile cleanly | passed | pass |
| Front/service smoke | `node --check app\\front\\assets\\executor-lab-standalone.js` and `.\\.venv\\Scripts\\python.exe -c "from app.front_lab_main import app; print(app.title)"` | Standalone JS parses and the standalone FastAPI app boots | passed | pass |

## Follow-up: Proactive Detection Moved Back Into Executor
- User clarified the architectural intent:
  - proactive interaction judgment must be owned by the executor itself
  - a second detector agent is not acceptable
  - the tuned existing executor prompt body must remain intact, but appending new rules is allowed
- Actions taken:
  - removed the separate proactive detector-agent path from `app/runtime/execution.py`
  - extended `ExecutorAgentTurnDraft` to carry optional `name` + `message_content`
  - appended a minimal proactive-output rule to the existing executor prompt without deleting or rewriting the original body
  - updated execution-loop handling and the standalone executor-lab runner to consume proactive payload directly from executor output
  - updated `tests/test_execution_memory_injection.py` to the new `_next_loop_executor_turn()` return shape

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Executor-owned proactive handoff slice | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_proactive_interaction.py tests\\test_execution_memory_injection.py tests\\test_executor_lab.py tests\\test_interaction.py tests\\test_execution_granularity.py tests\\test_memory.py -q` | Proactive handoff and executor-lab should stay green after removing the extra detector agent | 24 passed | pass |
| Syntax check | `.\\.venv\\Scripts\\python.exe -m py_compile app\\runtime\\execution.py app\\front\\executor_lab.py tests\\test_execution_memory_injection.py` | Touched files compile cleanly | passed | pass |

## Follow-up: Executor Now Sees Full Loop History
- User wants the executor itself to see the complete prior double-loop content, not just the last `scene/result`.
- Actions taken:
  - added a rendered `executor_history` block to the executor prompt
  - persisted prior rounds into `raw_data["executor_history"]`
  - each history item now includes:
    - roleplay reply
    - tool call summary
    - executor `scene/result/stop`
    - proactive handoff payload when present
  - aligned the standalone executor-lab initial payload with the same history format
  - added a focused prompt-level test to lock the new history block

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Executor history slice | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_execution_memory_injection.py tests\\test_executor_lab.py tests\\test_proactive_interaction.py tests\\test_interaction.py tests\\test_execution_granularity.py tests\\test_memory.py -q` | Executor prompt/history changes should keep runtime and lab behavior green | 25 passed | pass |
| Syntax check | `.\\.venv\\Scripts\\python.exe -m py_compile app\\runtime\\execution.py app\\front\\executor_lab.py tests\\test_execution_memory_injection.py` | Touched files compile cleanly | passed | pass |

## New Session Start: Interaction Cooldown Termination
- Switched the planning files to the new task: interaction should remain in a short waiting state after inbound or outbound chat, then fall back into one normal `replan` when the cooldown expires.
- User decisions captured:
  - default cooldown is `3` minutes
  - cooldown duration must be configurable
  - cooldown applies after both inbound replies and outbound proactive messages
  - timeout handling should call the normal `replan` path directly
  - implementation should stay as lean as possible and avoid new entities unless required
- Initial code search confirmed:
  - `InteractionService` currently owns the inbound/outbound chat execution only
  - `RuntimeOrchestrator` currently owns wake calculation and replan orchestration
  - `RuntimeState` currently has no interaction cooldown fields

## Interaction Cooldown Termination
- **Status:** completed
- Actions taken:
  - Added minimal cooldown state to `RuntimeState`:
    - `interaction_cooldown_until`
    - `interaction_cooldown_context`
    - `interaction_cooldown_resume_after_completion`
  - Added configurable `AMADEUS_INTERACTION_COOLDOWN_SECONDS` loading through `ExecutionSettings` with default `180`.
  - Wired `build_orchestrator(...)` to pass the cooldown setting into `RuntimeOrchestrator`.
  - Updated `RuntimeOrchestrator` so that:
    - inbound interaction records memory, emits replies, and starts cooldown without immediate replan
    - proactive outbound interaction also starts cooldown without immediate replan
    - active cooldown blocks due execution work
    - cooldown expiry synthesizes one timer wake and runs the normal replan decision/apply path
    - completion advance is resumed after timeout when the originating proactive execution had already exhausted the plan
  - Rewrote `tests/test_interaction.py` and `tests/test_proactive_interaction.py` into clean focused coverage for the new cooldown semantics.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Cooldown-focused syntax check | `.\\.venv\\Scripts\\python.exe -m py_compile app\\core\\state.py app\\infra\\settings.py app\\runtime\\orchestrator.py app\\main.py tests\\test_interaction.py tests\\test_proactive_interaction.py` | Touched runtime files and new tests compile cleanly | passed | pass |
| Cooldown-focused runtime tests | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_interaction.py tests\\test_proactive_interaction.py -q` | Inbound reset, timeout replan, and outbound cooldown should all work | `7 passed` | pass |
| Existing execution/memory slice | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_execution_memory_injection.py tests\\test_execution_granularity.py tests\\test_memory.py -q` | Existing execution + memory behavior should remain green | `20 passed` | pass |
| API spot check | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_api.py -q -k "post_message_runs_single_cycle or runtime_and_memory_inspection_endpoints or runtime_lifecycle_pause_and_resume_controls_scheduler or create_app_restores_latest_runtime_snapshot"` | Quick API smoke around `/api/messages` and runtime inspection | 4 failed | known baseline issue |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-04-11 | API spot-check still fails because the default app path has no configured `dialogue` model in tests, and the MCP startup path can still try to connect to the locally configured server | 1 | Left unchanged; this was a pre-existing environment/config baseline and is outside the cooldown implementation itself |

## New Session Start: Integrated Frontend Workspace
- Switched the planning files to the new task: build a real integrated frontend with sidebar navigation and four sections:
  - personas
  - workbench
  - chat
  - settings
- User-approved UX shape:
  - workbench shows active persona, runtime state, virtual time, and day plan
  - current plan item should highlight
  - execution details should expand per plan item
  - debug information should stay available but folded by default
- Current assumptions chosen to keep momentum:
  - first chat page will use a single message stream rather than multi-thread conversation management
  - first workbench version will rely on existing runtime/debug polling rather than a new product-facing stream endpoint

## Integrated Frontend Workspace
- **Status:** completed
- Actions taken:
  - Added a new integrated page:
    - `app/front/pages/workspace.html`
    - `app/front/assets/workspace.css`
    - `app/front/assets/workspace.js`
  - Switched the default front page from the standalone executor-lab to the new workspace page.
  - Kept `/front/debug` and `/front/executor-lab` intact for engineering use.
  - Added two thin aggregated APIs in `app/main.py`:
    - `/api/workspace/workbench`
    - `/api/workspace/chat`
  - Wired the integrated frontend to:
    - persona list / detail / soul editing / activation / deletion
    - runtime summary and current plan
    - virtual clock setting
    - per-plan execution record expansion
    - single-stream chat feed plus message sending
    - tool and MCP status
    - folded debug panels inside each main section
  - Updated API smoke tests so the root page now expects the integrated workspace instead of the standalone executor-lab.

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Backend syntax check | `.\\.venv\\Scripts\\python.exe -m py_compile app\\main.py` | New workspace endpoints should compile cleanly | passed | pass |
| Frontend syntax check | `node --check app\\front\\assets\\workspace.js` | New integrated frontend script should parse | passed | pass |
| Workspace smoke tests | `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_api.py -q -k "workspace_front_page_is_served or workspace_endpoints_return_integrated_payloads"` | Root page and new workspace APIs should respond correctly | `2 passed` | pass |
