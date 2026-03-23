# Progress Log

## Session: 2026-03-23

### Phase 1: Planning / Replan Implementation

- **Status:** complete
- Actions taken:
  - Re-read the README runtime loop and confirmed the gap between current fixed-template planning and the intended short-window planning behavior
  - Replaced the fixed planner with contextual routine/message/recovery planning
  - Made replan decisions live by applying replanned short windows back into orchestrator state
  - Anchored replanned windows after the current step slot to preserve schedule semantics
- Files created/modified:
  - `app/runtime/planning.py` (rewritten)
  - `app/runtime/replan.py` (rewritten)
  - `app/runtime/orchestrator.py` (updated)
  - `tests/test_replan.py` (created)
  - `tests/test_orchestrator.py` (updated)
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `task_plan.md` (rewritten)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Discovery: Remaining MVP Gaps

- **Status:** complete
- Actions taken:
  - Re-read the current planner, execution, replan, emotion, interaction, persona, and model-routing modules after landing the second MCP capability
  - Synthesized the current MVP gap list around cognition, replan application, interaction policy, and model usage rather than transport or MCP plumbing
- Files created/modified:
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read the current MCP builtins and planner trigger logic before choosing the second capability
  - Chose `search_web` as the next slice because it complements `read_url` without opening a new execution branch
  - Locked the trigger model to explicit search-intent phrases so the planner stays deterministic in MVP
- Files created/modified:
  - `task_plan.md` (rewritten for the second MCP slice)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Implementation

- **Status:** complete
- Actions taken:
  - Added builtin `search_web` support alongside `read_url`
  - Implemented structured search result handling and normalized partial-success behavior
  - Updated planner routing so explicit search-intent messages create a `search_web` tool step while URL messages still prefer `read_url`
  - Added app-level injection for a separate search HTTP client so verification stays deterministic
- Files created/modified:
  - `app/mcp/builtins.py` (updated)
  - `app/runtime/planning.py` (updated)
  - `app/main.py` (updated)
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added non-`tmp_path` capability coverage for `search_web`
  - Added non-`tmp_path` planner coverage for explicit search-intent routing and URL precedence
  - Re-ran `ruff check`, `compileall`, and the targeted non-`tmp_path` pytest suite
  - Verified the full `search: ... -> search_web -> outbound reply` API loop with a manual `httpx.MockTransport` + `TestClient` script
  - Deleted the generated verification files; the empty verification directory still remains because shell directory removal is blocked in this environment
- Files created/modified:
  - `tests/test_mcp_capabilities.py` (updated)
  - `tests/test_planning.py` (created)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read the current MCP registry, compat layer, planning service, and execution service before choosing the smallest real capability slice
  - Confirmed with the user that MVP should start with one external-information capability rather than a broader tool set
  - Chose `read_url` as the first concrete capability and chose URL-bearing user messages as the narrow planning trigger into tool mode
- Files created/modified:
  - `task_plan.md` (rewritten for the read_url MCP slice)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Implementation

- **Status:** complete
- Actions taken:
  - Extended `CapabilityRegistry` so each descriptor resolves to a real async executor
  - Added argument validation and exception normalization to `MCPCompatLayer`
  - Added builtin `read_url` capability registration plus HTML/text extraction logic
  - Updated planning so URL-bearing inbound messages create a `tool` step
  - Updated execution so tool steps call the MCP layer and emit normalized `tool_invocations`
  - Wired builtin capability registration into app/orchestrator construction and added injectable HTTP clients for deterministic tests
- Files created/modified:
  - `app/mcp/registry.py` (updated)
  - `app/mcp/compat.py` (updated)
  - `app/mcp/builtins.py` (created)
  - `app/runtime/planning.py` (updated)
  - `app/runtime/execution.py` (updated)
  - `app/main.py` (updated)
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added unit coverage for `read_url` extraction and invalid URL rejection
  - Added orchestrator coverage for URL-triggered tool execution
  - Re-ran `ruff check` and `compileall`
  - Confirmed that non-`tmp_path` pytest coverage passes
  - Verified the full `read_url -> /api/messages` loop with a manual `httpx.MockTransport` + `TestClient` script because sandbox ACL rules still break pytest tmpdir setup and cleanup
  - Removed the generated verification files; the now-empty verification directory could not be removed by shell policy
- Files created/modified:
  - `tests/test_mcp_capabilities.py` (created)
  - `tests/test_orchestrator.py` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read the README MVP boundary and runtime loop as the source of truth
  - Cross-checked the original concept document only to clarify the meaning of timers and heartbeat
  - Confirmed with the user that MVP should mean:
  - agent life progression runs
  - users can interrupt and interact
  - no heartbeat fallback in this phase
- Files created/modified:
  - `task_plan.md` (rewritten for the new runtime slice)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Documentation

- **Status:** complete
- Actions taken:
  - Updated the README to define MVP as schedule-driven rather than heartbeat-driven
  - Updated the runtime doc to distinguish scheduled wake-ups from heartbeat fallback
  - Recorded the decision that idle periods may remain quiet in MVP
- Files created/modified:
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Runtime Implementation

- **Status:** complete
- Actions taken:
  - Added per-step scheduling timestamps and hour-slot tracking to runtime state
  - Updated `PlanningService` to emit scheduled minute-step plans
  - Reworked the orchestrator so hour ticks create plans, due minute steps execute on schedule, and user messages immediately replace the current plan
  - Added a scheduler loop that waits for the next due step or next hour boundary instead of running a heartbeat poller
- Files created/modified:
  - `app/core/state.py` (updated)
  - `app/runtime/planning.py` (updated)
  - `app/runtime/orchestrator.py` (updated)
  - `app/main.py` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added orchestrator tests for hour-plan creation, scheduled minute-step progression, and message interrupts
  - Re-ran `ruff check`
  - Re-ran `compileall` across `app/` and `tests/`
  - Verified the new runtime flow with a pure in-memory script because sandbox permissions blocked temp-dir based pytest execution
- Files created/modified:
  - `tests/test_orchestrator.py` (rewritten)
  - `task_plan.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added a persisted persona profile service
  - Added `POST /api/persona/bootstrap` and `GET /api/persona`
  - Wired persona summary into runtime state and core memory
  - Updated planning so scheduled steps include persona-consistency guidance
  - Updated README and runtime docs to mention the persona endpoints
- Files created/modified:
  - `app/core/state.py` (updated)
  - `app/memory/service.py` (updated)
  - `app/persona/service.py` (updated)
  - `app/main.py` (updated)
  - `app/runtime/planning.py` (updated)
  - `tests/test_api.py` (updated)
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Re-ran `ruff check`
  - Re-ran `compileall` across `app/` and `tests/`
  - Verified persona bootstrap plus persona-aware planning with a pure in-memory script
  - Deleted the generated persona profile test file and noted that a failed sandbox temp directory could not be cleaned up by policy
- Added `next_wake_at` and next-step inspection fields to the message and runtime APIs
- Files created/modified:
  - `app/main.py` (updated)
  - `app/runtime/orchestrator.py` (updated)
  - `tests/test_api.py` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read `README.md` before choosing the next slice
  - Confirmed the README still expects a real `Raw / Core / Active / Archive` memory architecture
  - Identified that Archive Memory is currently only a schema and not a working subsystem
  - Chose a memory-focused slice that remains independent from the parallel QQ work
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Defined the next slice as active-memory retrieval plus archive compaction
  - Chose deterministic compaction instead of model-based summarization for the MVP
  - Chose to expose search through a generic API rather than transport-specific debugging
- Files created/modified:
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Replaced the webhook-first QQ plan with a gateway long-connection plan
  - Chose FastAPI lifespan hooks as the smallest way to manage the QQ adapter lifecycle
  - Added an explicit `AMADEUS_QQ_ENABLED` toggle so tests and local runs stay predictable
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Rewrote `app/communication/qq.py` around QQ gateway WebSocket mode
  - Added gateway handshake, heartbeat, resume, reconnect, and inbound event processing
  - Kept passive QQ reply sending and moved QQ startup/shutdown into FastAPI lifespan management
  - Removed the QQ callback route and updated env, docs, and scripts to match gateway mode
- Files created/modified:
  - `app/communication/qq.py` (updated)
  - `app/main.py` (updated)
  - `.env.example` (updated)
  - `docs/configuration.md` (rewritten)
  - `docs/runtime.md` (rewritten)
  - `scripts/runtime-config.ps1` (updated)
  - `scripts/start-server.ps1` (updated)
  - `scripts/start-tunnel.ps1` (updated)
  - `README.md` (updated)
  - `pyproject.toml` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Replaced callback-style QQ tests with gateway-mode adapter tests
  - Added a lifespan test for QQ adapter startup and shutdown
  - Re-synced dependencies after adding `websockets`
  - Re-ran lint and the full test suite
- Files created/modified:
  - `tests/test_api.py` (updated)
  - `tests/test_qq_adapter.py` (rewritten)
  - `progress.md` (updated)

### Phase 5: Delivery

- **Status:** complete
- Actions taken:
  - Finalized docs and examples around the new QQ long-connection setup
  - Prepared the project for the user-facing next step: enabling QQ and doing a real private-chat test
- Files created/modified:
  - `task_plan.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added rewrite support to the JSONL storage helper so active memory can compact in place
  - Added archive-memory persistence and configurable active-memory compaction
  - Upgraded retrieval from raw substring matching to lexical-plus-importance ranking
  - Added archive-aware memory search through `GET /api/memory/search`
  - Updated `GET /api/memory` and README/docs to reflect the Archive layer
  - Preserved the concurrently edited `task_plan.md` instead of overwriting the parallel QQ plan
- Files created/modified:
  - `app/infra/storage.py` (updated)
  - `app/memory/retrieval.py` (updated)
  - `app/memory/service.py` (updated)
  - `app/main.py` (updated)
  - `README.md` (updated)
  - `docs/runtime.md` (updated)
  - `docs/configuration.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added API coverage for archive fallback through the search endpoint
  - Added a service-level test for compaction and archive retrieval
  - Fixed retrieval scoring after one failed test iteration
  - Re-ran lint and the full test suite until green
- Files created/modified:
  - `tests/test_api.py` (updated)
  - `tests/test_memory_service.py` (created)
  - `app/memory/retrieval.py` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read the planning files before starting the next slice
  - Chose a workstream that is independent from the parallel QQ bot investigation
  - Identified that runtime snapshots are written but not restored on startup
  - Identified that core memory and active memory are not persisted across restarts
  - Identified the lack of generic runtime and memory inspection APIs
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Defined the next slice as runtime persistence plus inspection APIs
  - Chose to keep the work transport-agnostic so it does not conflict with QQ integration
  - Chose the smallest useful API surface: runtime state plus memory inspection
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added JSON and JSONL read helpers to the storage layer
  - Added snapshot lookup helpers
  - Updated `MemoryService` to load persisted raw, active, and core memory data
  - Restored the latest runtime snapshot into the orchestrator at app startup
  - Added `GET /api/runtime/state` and `GET /api/memory`
  - Documented the new persistence and inspection workflow
- Files created/modified:
  - `app/infra/storage.py` (updated)
  - `app/memory/snapshots.py` (updated)
  - `app/memory/service.py` (updated)
  - `app/main.py` (updated)
  - `docs/runtime.md` (updated)
  - `docs/configuration.md` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added API tests for runtime inspection and memory inspection
  - Added an end-to-end restart test that recreates the app and verifies snapshot restore
  - Re-ran lint and the full test suite
- Files created/modified:
  - `tests/test_api.py` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- **Started:** 2026-03-23
- Actions taken:
  - Read the planning-with-files skill instructions
  - Confirmed the current project directory contents
  - Ran session catch-up for the Amadeus folder
  - Confirmed there is no git repository in the current workspace root
  - Captured user requirements around virtual environments, model support, and immediate implementation
- Files created/modified:
  - `task_plan.md` (created)
  - `findings.md` (created)
  - `progress.md` (created)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Drafted implementation phases for project scaffolding
  - Recorded initial architectural and tooling decisions
  - Checked the available Python runtime and package manager tooling
  - Confirmed `Python 3.12` is available through `py -3.12`
- Files created/modified:
  - `task_plan.md` (created)
  - `findings.md` (created)
  - `progress.md` (created)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Installed `uv` into the Python 3.12 environment
  - Confirmed the target interpreter version is `Python 3.12.7`
  - Created a local `.venv` using `uv` and Python 3.12
  - Captured a new requirement to separate dialogue, decision, and memory model configuration
  - Added role-based model routing settings and router scaffolding
  - Updated README to document separate API/model configuration by workload
  - Added project metadata, package layout, core schemas, runtime stubs, memory stubs, MCP stubs, and tests
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** in_progress
- Actions taken:
  - Checked for tunnel tooling on the machine
  - Confirmed no `cloudflared` or `ngrok` is currently available on `PATH`
  - Chose to add stable local startup scripts before tackling public QQ callback ingress
  - Installed `cloudflared` via `winget` and located the executable path
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added PowerShell runtime scripts for `start`, `stop`, `status`, and `tail-logs`
  - Added a helper to read `AMADEUS_HOST` and `AMADEUS_PORT` from `.env`
  - Added runtime documentation for the local workflow
  - Iterated on Windows process detection until `status` and `stop` tracked the real server process correctly
- Files created/modified:
  - `scripts/start-server.ps1` (created and updated)
  - `scripts/stop-server.ps1` (created and updated)
  - `scripts/status-server.ps1` (created and updated)
  - `scripts/tail-logs.ps1` (created)
  - `scripts/runtime-config.ps1` (created and updated)
  - `docs/runtime.md` (created)
  - `.env.example` (updated)
  - `docs/configuration.md` (updated)
  - `.gitignore` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Verified the scripts by running real `start -> status -> stop` flows
  - Verified local runtime status after stop
  - Re-ran lint and the full test suite after script changes
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Reviewed the `claude-to-im` skill instructions for QQ-related scope and limitations
  - Read the local `qq-codex连接服务/使用说明.md`
  - Confirmed the existing QQ solution is a bridge to Codex/Claude rather than a native Amadeus channel adapter
  - Read the official QQ bot documentation for current credentials and callback signing
- Files created/modified:
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Reframed the next slice as a native QQ adapter inside `app/communication/qq.py`
  - Chose QQ webhook callback mode as the first native channel path
  - Limited the MVP scope to C2C private messages and passive text replies
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added `app/communication/qq.py`
  - Implemented QQ callback signature verification and challenge handling
  - Implemented C2C message mapping into `RuntimeEvent`
  - Implemented passive QQ reply sending through the QQ open API
  - Wired the adapter into FastAPI with a callback route
- Files created/modified:
  - `app/communication/qq.py` (created)
  - `app/main.py` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Added adapter-level challenge and callback tests
  - Verified the QQ callback route end to end with mocked QQ HTTP responses
  - Cleaned up lint issues in the new QQ adapter files
- Files created/modified:
  - `tests/test_qq_adapter.py` (created)
  - `app/communication/qq.py` (updated)
  - `app/main.py` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 5: Delivery

- **Status:** complete
- Actions taken:
  - Added a root `.env.example`
  - Added a short configuration document for model routing and QQ settings
  - Linked the new config docs from the README
  - Added automatic `.env` loading at app startup
  - Added a test covering `.env` loading
-  - Inspected the current `.env` before running an external model connectivity check
- Files created/modified:
  - `.env.example` (created)
  - `docs/configuration.md` (created)
  - `README.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)
  - `app/infra/env.py` (created)
  - `app/main.py` (updated)
  - `pyproject.toml` (updated)
  - `tests/test_env_loading.py` (created)

### Phase 1: Requirements & Discovery

- **Status:** complete
- Actions taken:
  - Re-read the planning files and skill instructions before the QQ bridge investigation
  - Inspected the local `claude-to-im` skill docs and config comments for QQ-specific clues
  - Traced the old QQ bridge through the local skill wrapper into the upstream `claude-to-im` library
  - Confirmed from source that the old QQ bridge uses `getAppAccessToken -> /gateway -> WebSocket` rather than an HTTP callback URL
  - Confirmed the old bridge still sends QQ replies through passive reply REST calls tied to inbound `message_id`
- Files created/modified:
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Started a real local uvicorn process against the current `.env`
  - Probed the running app over HTTP instead of relying only on unit tests
  - Verified `health`, `message`, and QQ callback route registration locally
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)
  - `README.md` (updated)
  - `app/infra/settings.py` (created)
  - `app/infra/model_client.py` (updated)
  - `app/main.py` (updated)
  - `tests/test_settings.py` (created)
  - `pyproject.toml` (created and updated)
  - `app/` package skeleton (created)
  - `tests/` (created)

### Phase 4: Testing & Verification

- **Status:** complete
- Actions taken:
  - Synced project dependencies into the local `.venv`
  - Fixed Hatch packaging metadata for the local package
  - Fixed a Pydantic recursive alias issue in core schemas
  - Fixed style and async test issues revealed by verification
  - Re-ran tests and lint until both passed
- Files created/modified:
  - `pyproject.toml` (updated)
  - `app/core/types.py` (updated)
  - `app/memory/service.py` (updated)
  - `app/infra/model_client.py` (updated)
  - `app/infra/settings.py` (created)
  - `tests/test_orchestrator.py` (updated)
  - `README.md` (updated)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 2: Planning & Structure

- **Status:** complete
- Actions taken:
  - Re-read planning files before starting the next feature slice
  - Chose the minimal next slice: a FastAPI-driven single-message closed loop
  - Updated the plan from scaffold delivery to message loop implementation
  - Identified `app/main.py`, `app/communication/hub.py`, and new API tests as the smallest change surface
- Files created/modified:
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 3: Implementation

- **Status:** complete
- Actions taken:
  - Added a drain operation to the communication hub
  - Added request and response models for the first message loop API
  - Implemented `POST /api/messages` to create a `message_received` runtime event
  - Returned the orchestration outcome, outbound messages, and runtime state summary in one response
  - Added an API test covering one full request cycle
- Files created/modified:
  - `app/communication/hub.py` (updated)
  - `app/main.py` (updated)
  - `tests/test_api.py` (created)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

### Phase 4: Role Simulation Runtime

- **Status:** complete
- Actions taken:
  - Replaced narrative execution with actual reply generation for both narrative and hybrid tool steps
  - Added real interaction policy logic with model-backed and heuristic paths
  - Upgraded persona bootstrap to structured extraction and emotion updates to continuous scoring
  - Made memory ingest inbound messages and feed planning/execution context
  - Rewired runtime services so `dialogue / decision / memory` model routes are actually consumed
  - Reworked the relevant runtime/API tests to use shared in-memory stores instead of `tmp_path`
- Files created/modified:
  - `app/main.py` (updated)
  - `app/memory/retrieval.py` (updated)
  - `app/memory/service.py` (updated)
  - `app/persona/service.py` (updated)
  - `app/runtime/emotion.py` (updated)
  - `app/runtime/execution.py` (updated)
  - `app/runtime/interaction.py` (updated)
  - `app/runtime/orchestrator.py` (updated)
  - `app/runtime/planning.py` (updated)
  - `tests/test_api.py` (updated)
  - `tests/test_memory_service.py` (updated)
  - `tests/test_orchestrator.py` (updated)
  - `tests/test_role_simulation.py` (created)
  - `tests/test_support.py` (created)
  - `task_plan.md` (updated)
  - `findings.md` (updated)
  - `progress.md` (updated)

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Ruff after role-simulation slice | `.venv\Scripts\python.exe -m ruff check app tests` | No lint violations after dialogue/persona/memory wiring | All checks passed | PASS |
| Pytest after role-simulation slice | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_role_simulation.py tests/test_planning.py tests/test_replan.py tests/test_orchestrator.py tests/test_api.py tests/test_memory_service.py` | Updated runtime, API, and role-simulation tests pass in the sandbox | 20 tests passed | PASS |
| Compileall after role-simulation slice | `.venv\Scripts\python.exe -m compileall app tests` | Updated runtime and test modules compile successfully | Completed successfully | PASS |
| Ruff after planning/replan slice | `.venv\Scripts\python.exe -m ruff check app tests` | No lint violations after planning/replan rewrite | All checks passed | PASS |
| Pytest after planning/replan slice | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_mcp_capabilities.py tests/test_planning.py tests/test_replan.py` | Planning, replan, and MCP non-`tmp_path` tests pass | 8 tests passed | PASS |
| Compileall after planning/replan slice | `.venv\Scripts\python.exe -m compileall app tests` | Rewritten planning and replan modules compile successfully | Completed successfully | PASS |
| Ruff after search_web slice | `.venv\Scripts\python.exe -m ruff check app tests` | No lint violations after second MCP capability | All checks passed | PASS |
| Pytest after search_web slice | `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_mcp_capabilities.py tests/test_planning.py` | Non-`tmp_path` capability and planning tests pass | 5 tests passed | PASS |
| Compileall after search_web slice | `.venv\Scripts\python.exe -m compileall app tests` | Updated app and tests compile successfully | Completed successfully | PASS |
| Manual search_web API verification | Inline script with `httpx.MockTransport` + `TestClient` | Explicit search-intent message produces `search_web` tool outcome and outbound reply | Verification script passed | PASS |
| Ruff after read_url MCP slice | `.venv\Scripts\python.exe -m ruff check app tests` | No lint violations after MCP/tool changes | All checks passed | PASS |
| Compileall after read_url MCP slice | `.venv\Scripts\python.exe -m compileall app tests` | Updated app and tests compile successfully | Completed successfully | PASS |
| MCP capability pytest | `.venv\Scripts\python.exe -m pytest tests/test_mcp_capabilities.py` | `read_url` tests pass | 2 tests passed | PASS |
| Manual read_url API verification | Inline script with `httpx.MockTransport` + `TestClient` | URL message produces tool outcome and outbound reply | Verification script passed | PASS |
| Session catch-up | `session-catchup.py` | Recover prior context if any | No catch-up output required | PASS |
| Git status | `git status --short` | Show working tree state | Failed because not a git repo | NOT_APPLICABLE |
| Python version | `python --version` | Python 3.12 if already installed | Python 3.10.11 | INFO |
| uv availability | `uv --version` | uv available | Command not found | INFO |
| Python launcher inventory | `py -0p` | Show whether Python 3.12 exists | Python 3.12 is available | PASS |
| uv installation | `py -3.12 -m pip install uv` | Install uv successfully | Installed `uv 0.10.12` | PASS |
| Python 3.12 check | `py -3.12 --version` | Confirm usable Python 3.12 | Python 3.12.7 | PASS |
| Virtual environment creation | `py -3.12 -m uv venv .venv --python 3.12` | Create local venv | `.venv` created successfully | PASS |
| Dependency sync | `py -3.12 -m uv sync --all-groups` | Install project and dependencies into `.venv` | Succeeded after package config fix | PASS |
| Ruff | `py -3.12 -m uv run ruff check .` | No lint violations | All checks passed | PASS |
| Pytest | `py -3.12 -m uv run pytest` | Test suite passes | 5 passed | PASS |
| Message loop API | `POST /api/messages` via `TestClient` | One orchestration cycle completes and returns outbound messages | Test passed | PASS |
| Ruff after API slice | `py -3.12 -m uv run ruff check .` | No lint violations after new endpoint | All checks passed | PASS |
| Pytest after API slice | `py -3.12 -m uv run pytest` | Full suite passes after new endpoint | 6 passed | PASS |
| QQ adapter tests | `py -3.12 -m uv run pytest` | Challenge flow and callback flow both pass | Included in 8 passing tests | PASS |
| Ruff after QQ slice | `py -3.12 -m uv run ruff check .` | No lint violations after QQ adapter | All checks passed | PASS |
| Pytest after QQ slice | `py -3.12 -m uv run pytest` | Full suite passes after QQ adapter | 8 passed | PASS |
| Local uvicorn connectivity | Real uvicorn process + HTTP probes | Core local routes are reachable | `/health` ok, `/api/messages` success, QQ callback route present | PASS |
| Runtime script status after fix | `status-server.ps1` while service is running | Reports running and health ok | PASS |
| Runtime script stop after fix | `stop-server.ps1` | Stops the real server listener | PASS |
| Runtime script final status | `status-server.ps1` after stop | Reports stopped | PASS |
| Ruff after runtime scripts | `py -3.12 -m uv run ruff check .` | No lint violations after script/docs updates | All checks passed | PASS |
| Pytest after runtime scripts | `py -3.12 -m uv run pytest` | Full suite passes after runtime script changes | 9 passed | PASS |
| Dependency sync after QQ gateway rewrite | `py -3.12 -m uv sync --all-groups` | Install new WebSocket dependency cleanly | PASS | PASS |
| Ruff after QQ gateway rewrite | `py -3.12 -m uv run ruff check .` | No lint violations after transport rewrite | All checks passed | PASS |
| Pytest after QQ gateway rewrite | `py -3.12 -m uv run pytest` | Full suite passes after transport rewrite | 13 passed | PASS |
| Ruff after archive memory slice | `.venv\Scripts\python.exe -m ruff check .` | No lint violations after archive/search changes | All checks passed | PASS |
| Pytest after archive memory slice | `.venv\Scripts\python.exe -m pytest` | Full suite passes after archive/search changes | 13 passed | PASS |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-03-23 | `git status --short` outside a git repository | 1 | Logged and continued without git-based steps |
| 2026-03-23 | `uv --version` command not found | 1 | Logged and investigating interpreter/tool availability |
| 2026-03-23 | `uv sync` failed because Hatch could not infer the package path | 1 | Added explicit wheel package configuration for `app` |
| 2026-03-23 | `pytest` collection failed due to recursive `JsonValue` alias | 1 | Simplified the alias and prepared a code cleanup pass |
| 2026-03-23 | `ruff check` reported import and typing issues | 1 | Cleaning up code style and type declarations before rerun |
| 2026-03-23 | `ruff check` reported `app/main.py` import ordering and a long line after adding the API endpoint | 1 | Reordered imports and wrapped the list comprehension |
| 2026-03-23 | `app/main.py` initially returned before registering the QQ route | 1 | Moved the `return app` below route registration |
| 2026-03-23 | `ruff check` reported typing and line length issues in the QQ adapter files | 1 | Updated imports and wrapped long return lines |
| 2026-03-23 | Start script produced a stale PID after launch | 1 | Switched the launcher to `.venv\\Scripts\\python.exe -m uvicorn` |
| 2026-03-23 | Archive-memory retrieval test returned active hits with no lexical match | 1 | Require lexical match before importance affects ranking |
| 2026-03-23 | `pytest` tmpdir setup and cleanup failed with `PermissionError` in this sandbox | 1 | Kept the new tests, redirected verification to `ruff`, `compileall`, and a manual API script |
| 2026-03-23 | Shell deletion of the verification directory was blocked by policy | 1 | Deleted the generated verification files individually and left the empty directory in place |
| 2026-03-23 | Full runtime tests for the second MCP capability would hit the same tmpdir ACL limitation | 1 | Focused second-slice pytest coverage on non-`tmp_path` tests and added a manual API verification for `search_web` |
| 2026-03-23 | The planning rewrite initially triggered Ruff line-length errors and regex escape warnings | 1 | Wrapped the long planning strings and converted the search-intent regex boundaries to raw fragments |
| 2026-03-23 | `tmp_path` and even project-local `--basetemp` still failed under sandbox ACLs for runtime/API tests | 1 | Replaced the affected tests with in-memory store harnesses so the suite can pass locally in this environment |
| 2026-03-23 | Memory retrieval recency scoring initially surfaced unrelated active memories | 1 | Applied the recency bonus only after a lexical hit exists |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 5: Delivery |
| Where am I going? | Next feature slice after the native QQ adapter MVP |
| What's the goal? | Implement a native QQ webhook adapter for Amadeus |
| What have I learned? | See findings.md |
| What have I done? | Replaced the native QQ callback loop with a gateway long-connection loop and verified it end to end in tests |
