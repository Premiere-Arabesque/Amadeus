# Findings & Decisions

## Requirements

- Use the `planning-with-files` skill to organize work
- Manage dependencies with a virtual environment
- Support both OpenAI and Anthropic APIs
- Use the agreed MVP stack:
- `Python 3.12`
- `uv`
- `asyncio`
- `FastAPI`
- `Pydantic`
- `SQLAlchemy + SQLite`
- `JSONL Raw Log`
- `PydanticAI`
- official model SDKs
- official MCP Python SDK
- `pytest`
- `Ruff`
- Start implementation now rather than stopping at planning
- Support separate API/model configuration for user dialogue vs decision/replan vs memory processing

## Research Findings

- The remaining role-simulation stubs are now real MVP implementations rather than placeholders.
- `ExecutionService` now generates actual dialogue for narrative steps and conversational tool replies for hybrid steps; it no longer returns `Executed step: ...`.
- `InteractionPolicy` now has real `respond_now` vs `record_only` behavior, with a decision-model path plus heuristic fallback.
- `PersonaService` now performs structured persona extraction into summary, stable traits, relationship context, and preferences instead of simple truncation.
- `MemoryService` now converts inbound messages and outcomes into active memory notes, and `PlanningService` now reads recalled memory context back into new short-window steps.
- `EmotionService` now updates valence/arousal/dominance as a continuous state instead of only flipping between two fixed summaries.
- `create_app()` and `build_orchestrator()` now wire the configured `dialogue / decision / memory` model routes into the runtime services instead of leaving the router mostly unused.
- To keep verification working in this sandbox, the main runtime/API tests were moved to shared in-memory store harnesses instead of `tmp_path`.
- After this slice, the main remaining product risk is not placeholder runtime code, but the quality of the prompts and the quality of the external models configured for those routes.

- After landing real plan/replan behavior, the main blockers to a believable interactive role simulation are no longer scheduling or MCP plumbing.
- The next product-critical layer is dialogue generation: narrative steps still return generic execution summaries and user-message policy still hardcodes immediate replies.
- The next reasoning layer is persona/emotion/model usage: persona is still summarized by truncation, emotion is still a small heuristic, and runtime logic still does not consume the configured role-based model router.
- The next continuity layer is memory consumption: memory persists and can be searched, but planning/dialogue still do not materially retrieve and use remembered context.
- If the user wants interaction through the app API, those three layers are the real remaining blockers; if the user wants phone/QQ-style interaction, live channel validation is an additional final step.

- Planning is no longer a single fixed three-step template; it now varies by event type, time-of-day routine, persona hints, and degraded-outcome recovery context.
- `ReplanService` is no longer a placeholder binary success/failure stub; it now requests a micro replan when a short window is exhausted or the previous outcome degrades.
- `RuntimeOrchestrator` now applies replan decisions back into state by generating a new short window instead of merely computing a decision and discarding it.
- Replanned windows are anchored after the current step's scheduled slot, which keeps the schedule-driven runtime from collapsing multiple fresh windows into the same clock instant.
- Tool-trigger routing for `read_url` and `search_web` remains intact after the planning rewrite.
- The current planning layer is now functional for MVP, but it is still heuristic and not yet model-backed.
- New non-`tmp_path` tests cover both replan decisions and orchestrator state mutation for replans.

- After `read_url` and `search_web`, the biggest remaining MVP gaps are no longer channel or MCP plumbing, but the still-stubbed cognition layers above them.
- `PlanningService` is schedule-aware, but its actual plan content is still deterministic template text rather than model-backed life planning.
- `ReplanService` only returns a binary success/failure decision, and the orchestrator currently does not apply that decision to mutate the plan.
- `ExecutionService` only becomes real for tool steps; narrative steps still collapse to a generic `Executed step: ...` outcome.
- `InteractionPolicy` still treats every inbound user message as `RESPOND_NOW`, so there is no meaningful interrupt/defer/record-only policy yet.
- `PersonaService` still turns bootstrap text into a truncated summary; it does not perform structured persona extraction or iterative completion.
- `EmotionService` is still a two-branch heuristic, not a richer persona-aware emotional state update.
- `ModelRouter` exists, but the runtime still instantiates a direct `PydanticAIModelClient` and does not actually route planning/replan/persona work through the configured role-based model settings.
- Memory persistence is present, but retrieval remains lexical-only and planning does not yet materially consume retrieved memory as part of decision making.
- The QQ transport layer exists in code, but the product-level MVP still lacks confirmation that the live channel behavior is stable under real operator usage.

- The second MCP capability is now `search_web`, which complements `read_url` without changing the existing MCP execution path.
- The MVP search trigger is intentionally explicit and narrow: `search: ...`, `搜一下 ...`, `查一下 ...`, and similar direct search-language prefixes.
- URL-bearing messages still take precedence and remain routed to `read_url`, even if they also contain search language.
- `search_web` is currently implemented against the DuckDuckGo Instant Answer JSON format rather than a broader crawler or full browser search stack.
- `search_web` returns `success` when it finds an abstract or structured result hits, and `partial_success` when the provider returns no structured leads.
- The builtins registration path now exposes both `read_url` and `search_web`, and app construction supports a separate injected HTTP client for search verification.
- `tests/test_mcp_capabilities.py` now covers structured `search_web` results, and `tests/test_planning.py` covers search-intent routing plus URL-precedence behavior.
- `ruff`, `compileall`, and the non-`tmp_path` pytest suite for MCP/planning all pass after the second capability slice.
- A manual `TestClient` verification confirmed the end-to-end `search: ... -> search_web -> outbound reply` API loop.

- The first real MVP MCP capability is now `read_url`, deliberately scoped below a broader `search_web` stack.
- `CapabilityRegistry` now stores both descriptors and executors, so the MCP layer can resolve a real capability instead of only returning a stub.
- `MCPCompatLayer` now normalizes three MVP failure cases before execution returns: unknown capability, missing required arguments, and executor-level exceptions.
- `PlanningService` now treats a URL-bearing inbound message as the narrowest reliable tool trigger and emits a `tool` step with `capability="read_url"`.
- `ExecutionService` now turns tool steps into real MCP calls and records normalized `tool_invocations` on the outcome.
- `build_orchestrator()` and `create_app()` now register builtin capabilities on startup and allow an injected HTTP client for deterministic tests.
- `read_url` fetches an `http` or `https` page, strips basic HTML noise, extracts readable text, and returns a unified outcome payload.
- `tests/test_mcp_capabilities.py` covers successful HTML extraction and invalid-URL rejection for the new capability.
- `tests/test_orchestrator.py` now includes runtime coverage for a URL-bearing user message that becomes a `read_url` tool step.
- `ruff` and `compileall` both pass after the MCP slice.
- In this sandbox, `pytest` can run non-`tmp_path` tests, but `tmpdir` setup and cleanup still hit ACL errors even with a project-local `--basetemp`.
- A manual verification script using `httpx.MockTransport` and `TestClient` confirmed the full `read_url -> tool outcome -> /api/messages reply` loop.

- The user clarified that MVP scope should follow `README.md` rather than the earlier, more ambitious concept draft.
- Under that README interpretation, the MVP core is:
- agent life progression must run
- user messages must interrupt and interact with that progression
- `主动触达` is not yet a clearly defined MVP requirement
- The README says `交互层` or `定时器` produce events, but the user clarified the timer should be schedule-driven rather than a fixed heartbeat loop.
- The approved MVP runtime model is:
- planned action timepoints drive execution
- action completion drives replan
- user messages interrupt immediately
- idle periods may remain quiet
- no heartbeat fallback in MVP
- Removing heartbeat does not remove scheduling; the runtime still needs a wake-up mechanism for the next due plan step or next hour boundary.
- The original concept document is useful as design background, but its `5-minute heartbeat`, always-on daemon, and proactive-message framing are now above the README-scoped MVP.
- The runtime state now includes per-step scheduling timestamps plus the current hour planning slot.
- `PlanningService` now emits scheduled minute steps instead of an unscheduled single action.
- `RuntimeOrchestrator` now has:
- hour-boundary planning
- due-step execution based on scheduled plan timepoints
- message interrupts that replace the current plan immediately
- a scheduler loop that waits for the next due action rather than polling on a fixed heartbeat
- The new MVP runtime still allows quiet idle periods; when the current short plan is exhausted, the next wake-up is the next hour boundary unless a user message arrives first.
- The README MVP loop explicitly includes `人设初始化`, so leaving persona as a stub would keep the closed loop incomplete.
- A minimal persona slice can stay deterministic for now:
- accept seed text
- persist a profile
- project the summary into runtime state and core memory
- let planning read that summary for behavioral consistency
- The codebase now includes a persisted persona profile plus `POST /api/persona/bootstrap` and `GET /api/persona`.
- Because the runtime is now schedule-driven, inspection APIs need to surface `next_wake_at` and the next pending step; otherwise local debugging stays opaque.

- The planning-with-files workflow requires persistent `task_plan.md`, `findings.md`, and `progress.md` in the project root.
- Another agent is already researching QQ bot access details, so the best parallel slice is runtime persistence and observability rather than more channel-specific changes.
- The current `Amadeus` directory contains only markdown documents and no code yet.
- The project root is not currently inside a git repository.
- The default `python` on this machine is `Python 3.10.11`.
- `uv` is not currently installed or not available on `PATH`.
- `py -0p` shows that `Python 3.12` is available via the Windows launcher.
- `py` itself is available on `PATH`, so we can target `py -3.12` explicitly instead of relying on the default `python`.
- `uv` was successfully installed into the Python 3.12 environment via `py -3.12 -m pip install uv`.
- `py -3.12 --version` reports `Python 3.12.7`.
- A project-local virtual environment was created at `.venv` using `py -3.12 -m uv venv .venv --python 3.12`.
- The code skeleton now includes role-based model routing for `dialogue`, `decision`, and `memory`.
- The next useful slice is a minimal single-user message loop over FastAPI rather than deeper internal abstractions.
- The project now exposes a working `POST /api/messages` endpoint that runs one orchestration cycle and returns outbound messages and runtime state.
- The existing `claude-to-im` skill and local `qq-codex连接服务` are useful as QQ channel references, but they are built around bridging to Claude/Codex runtimes rather than directly to Amadeus.
- The current local QQ bridge documentation confirms practical constraints for MVP channel work: Windows supervisor scripts already exist, QQ support is C2C private chat only, and sandbox/private-friend setup is required.
- Official QQ bot docs confirm several key integration facts:
- `AppID` and `AppSecret` are the current credentials, while the old `Token` auth path is deprecated.
- QQ callback signatures use `Ed25519` with `X-Signature-Ed25519` and `X-Signature-Timestamp`.
- QQ developer docs provide webhook events and message send APIs under the official QQ bot documentation site.
- The official BotGo repository README confirms a practical webhook setup path for QQ bots, including registering `C2C_MESSAGE_CREATE` and configuring a callback URL.
- The QQ adapter now exists natively in the codebase and handles signature verification, challenge response, C2C message ingestion, and passive text replies.
- The project now has a root `.env.example` and `docs/configuration.md` describing model routing and QQ settings.
- The application now auto-loads the project root `.env` during app creation, so local configuration does not need to be manually exported into the shell first.
- Local connectivity is now verified with a real uvicorn process:
- `/health` returned `ok`
- `/api/messages` completed one runtime cycle successfully
- `/api/qq/callback` is registered in the FastAPI OpenAPI schema
- This machine does not currently have `cloudflared` or `ngrok` on `PATH`, so public QQ callback testing still needs a tunnel tool or another public ingress option.
- `cloudflared` has now been installed successfully via `winget`.
- On this machine, the executable is located at `C:\Program Files (x86)\cloudflared\cloudflared.exe`; the current shell session just has not picked it up on `PATH` yet.
- The project now includes local `start/status/stop/logs` PowerShell scripts plus a runtime guide.
- Runtime scripts now read `AMADEUS_HOST` and `AMADEUS_PORT` from `.env`, defaulting to `127.0.0.1:8010`.
- After several iterations, the script workflow is verified: `start` brings the service up, `status` reports running/stopped correctly, and `stop` terminates the real listener process.
- The current `.env` appears to still use a placeholder value for `DASHSCOPE_API_KEY`, so a real model connectivity test may fail for configuration reasons rather than network reasons.
- The current runtime already persists raw events and snapshots to disk, but app startup does not restore the latest snapshot into the orchestrator state.
- `MemoryService` keeps `core_memory` and `active_entries` only in memory today, so they are lost after a process restart.
- The current FastAPI surface exposes `/health`, `/api/messages`, and the QQ callback, but there is no generic runtime or memory inspection API yet.
- The runtime now restores the latest saved snapshot during app creation, so state continuity survives a normal service restart.
- `MemoryService` now persists `core_memory` and `active_entries` under `memory/`, making recent memory inspectable across restarts.
- FastAPI now exposes `GET /api/runtime/state` and `GET /api/memory`, which provide transport-agnostic inspection endpoints for local debugging and integration work.
- The README still calls for a full `Raw / Core / Active / Archive` memory architecture, but the current implementation only materially supports `Raw`, `Core`, and `Active`.
- `ArchiveMemoryEntry` exists as a schema only; there is not yet a persistence path, compaction rule, or retrieval path that actually uses it.
- `MemoryRetriever` currently only does a basic substring match over active memory entries and does not participate in any API surface.
- The project planning file is currently being reused by a parallel QQ gateway slice, so this memory slice should be tracked in `findings.md` and `progress.md` without overwriting that plan.
- Archive memory is now persisted to `memory/archive_memory.jsonl`, and active memory compacts itself when it exceeds a configurable cap.
- The memory inspection API now includes archive entries, and the project exposes `GET /api/memory/search` for transport-agnostic memory lookup.
- Retrieval scoring needed one correction during implementation: lexical match must exist before `importance` can affect ranking, otherwise unrelated memories surface.
- The old `claude-to-im` QQ bridge does not rely on an HTTP callback URL for QQ.
- Its QQ path is implemented in the upstream `claude-to-im` library adapter, not in the local skill wrapper.
- The upstream QQ adapter explicitly states it uses the QQ WebSocket gateway for inbound events and REST for outbound sends.
- The QQ flow in that bridge is:
- `App ID + App Secret -> getAppAccessToken -> GET /gateway -> WebSocket connect -> HELLO/IDENTIFY -> C2C_MESSAGE_CREATE`
- The old bridge still uses passive replies for QQ outbound messages, so outbound sends must reference the inbound `message_id` and increment `msg_seq`.
- The practical reason the old bridge only asked for `App ID + Secret` is that it opens a gateway connection itself, so QQ can push events over WebSocket and no public webhook URL is needed.
- If Amadeus wants the same UX, the clean path is to replace the current FastAPI webhook-style QQ adapter with a long-lived QQ gateway adapter that runs inside the process or under the orchestrator, not beside it.
- The Amadeus QQ adapter has now been switched to gateway long-connection mode.
- FastAPI now manages the QQ adapter lifecycle with startup and shutdown hooks instead of exposing a QQ callback route.
- The QQ configuration now uses an explicit `AMADEUS_QQ_ENABLED` flag so tests and local runs do not accidentally open a QQ gateway session just because credentials exist in `.env`.
- The current QQ MVP behavior is:
- fetch access token
- fetch gateway URL
- connect WebSocket
- respond to `HELLO` with `IDENTIFY` or `RESUME`
- process `C2C_MESSAGE_CREATE`
- send passive REST replies with `msg_id` and `msg_seq`

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Build a minimal code skeleton first | Lets us validate imports, dependency setup, and module boundaries early |
| Use `core/` for shared schemas | Keeps framework-specific types out of business logic |
| Put OpenAI/Anthropic support behind `infra/model_client.py` | Preserves replaceability and keeps the rest of the code model-agnostic |
| Keep first runtime slice small | Faster path to a working orchestrator and lower refactor cost |
| Use `py -3.12` rather than the default `python` executable | Ensures the project actually uses the requested Python 3.12 runtime |
| Install and invoke `uv` through Python 3.12 first | Avoids ambiguity while the shell `PATH` has not been refreshed |
| Use a project-local `.venv` | Keeps environment management explicit and isolated per the user request |
| Add role-based model routing early | Different workloads need different price/performance tradeoffs, and retrofitting this later would touch multiple modules |
| Treat dialogue, decision, and memory as separate model roles | Matches the product requirement that cheaper APIs may be used for non-dialogue workloads |
| Make the first real channel loop HTTP-first | It is the shortest path to a working end-to-end runtime cycle and is easy to test |
| Return drained outbound messages in the API response for MVP | Gives us a complete first closed loop without introducing transport delivery state yet |
| Treat the existing QQ bridge as a reference or sidecar candidate, not as the Amadeus channel layer itself | The bridge is runtime-specific today and would otherwise couple Amadeus to Claude/Codex assumptions |
| Use native QQ webhook callbacks rather than wrapping the old Codex bridge | This keeps the channel layer aligned with Amadeus' own runtime and FastAPI entrypoints |
| Implement QQ passive replies through a thin adapter instead of adding a heavyweight SDK abstraction first | The MVP needs a working channel loop more than a generalized transport stack |
| Auto-load `.env` from the project root during app creation | The user is configuring local development through `.env`, so startup should honor that by default |
| Validate connectivity with a real local uvicorn process before external QQ testing | This separates local app issues from public callback / platform issues |
| Add local start/stop/status scripts before external webhook testing | They give us a repeatable runtime baseline and persistent logs for QQ callback debugging |
| Let runtime scripts use `.env` host/port and detect the real listening process by port | This is more robust on Windows than trusting the launcher PID |
| Use `cloudflared` for the first public ingress attempt | It is simpler than `ngrok` here because it can create a quick tunnel without extra account setup |
| Preflight configuration before model connectivity tests | Distinguishes bad credentials from transport/network problems and saves misleading failures |
| Prioritize runtime persistence and inspection as the next slice | It is valuable on its own and does not block or interfere with QQ integration work |
| Restore orchestrator state from the latest snapshot on startup | This gives the app a meaningful continuity win with minimal scope |
| Persist core and active memory in their own files | These are the first memory views we want to inspect directly without replaying raw logs |
| Add generic inspection endpoints instead of transport-specific debug code | This keeps the debugging surface reusable across API, QQ, and future channels |
| Validate restart persistence through app re-creation in tests | It proves the recovery path instead of only testing individual helper methods |
| Use the README memory architecture to choose the next independent slice | This keeps implementation work aligned with the intended product shape |
| Implement archive memory through deterministic compaction first | This creates real Archive Memory behavior without depending on expensive summarization models yet |
| Keep search lexical plus importance-aware in MVP | It is simple, testable, and enough to validate the retrieval surface before embeddings |
| Prefer the QQ gateway long-connection model over webhook callbacks if we want the same operator experience as the old bridge | It removes the need for a public callback URL and matches the already-proven local bridge design |
| Gate QQ startup behind `AMADEUS_QQ_ENABLED` | This avoids surprise network connections in tests and local tooling while still enabling the desired production UX |
| Preserve the concurrently edited `task_plan.md` and log this slice elsewhere | Another agent is actively using that file for QQ transport work |
| Make archive compaction configurable through the service constructor | It keeps the default simple while making tests and future tuning easier |
| Let archive hits act as fallback behind active hits | This matches the README's intended memory-layer behavior |
| Make `read_url` the first real MCP capability | It is the smallest external-information action that proves the MCP execution path end to end |
| Trigger tool mode only when a user message already contains a URL | This keeps planning deterministic and avoids inventing a broader action-selection policy too early |
| Allow HTTP client injection for `read_url` during app construction | It keeps tests deterministic without special-case code paths in runtime logic |
| Add `search_web` as the second real MCP capability | It extends the MVP's external-information ability without introducing a new runtime branch |
| Make search triggering explicit rather than implicit | It keeps tool selection deterministic and avoids turning every question into a web lookup |
| Use DuckDuckGo Instant Answer as the MVP search provider shape | It gives a lightweight structured response format without adding credentials or browser automation |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Repository is not under git | Proceed without git tooling and avoid git-dependent steps |
| Requested MVP stack uses `Python 3.12 + uv`, but current shell exposes `Python 3.10.11` and no `uv` | Need to detect available Python interpreters and either use `py -3.12` or install the missing tooling |
| `uv sync` could not build the local package | Explicitly configure Hatch to package the `app` directory |
| Pydantic recursion during test collection | Avoid recursive generic aliasing for arbitrary JSON payload values in the first MVP slice |
| Ruff violations in scaffolded files | Clean up import ordering and modernize a few type declarations |
| Startup PID tracking was wrong when the script launched through `py -m uv run` | Launch the local `.venv` Python process directly so status/stop target the real uvicorn server |
| Runtime state survives to disk but not back into memory on restart | Add read helpers and restore logic rather than changing transport code |
| Retrieval ranking initially returned unmatched entries because importance alone could yield a positive score | Require a lexical match before importance contributes to rank |
| `pytest` tmpdir setup and cleanup hit sandbox ACL restrictions | Keep pytest coverage in place, but validate the URL tool loop with `ruff`, `compileall`, and a manual API script in this environment |
| Runtime tests that depend on `tmp_path` remain hard to execute in this sandbox | Keep second-capability verification focused on non-`tmp_path` pytest plus a manual search API script |

## Resources

- `c:\Users\lenovo\Desktop\agent\Amadeus\README.md`
- `C:\Users\lenovo\.codex\skills\planning-with-files\SKILL.md`

## Visual/Browser Findings

- None yet
