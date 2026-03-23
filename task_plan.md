# Task Plan: Real Role Simulation Runtime

## Goal

Replace the remaining role-simulation stubs with real MVP behavior: dialogue should produce actual in-character replies, persona and memory should materially shape runtime behavior, interaction policy should stop being hardcoded, and the updated runtime should be covered by tests that work in this environment.

## Current Phase

Phase 5

## Phases

### Phase 1: Re-read Remaining Stubs

- [x] Re-read `execution`, `interaction`, `persona`, `emotion`, `memory`, and runtime wiring
- [x] Confirm which layers were still placeholder implementations
- [x] Decide where model routing should be consumed in MVP
- **Status:** complete

### Phase 2: Runtime Implementation

- [x] Replace narrative execution with real dialogue generation
- [x] Replace hardcoded interaction policy with actual decision logic
- [x] Upgrade persona bootstrap from truncation to structured extraction
- [x] Upgrade emotion updates from a binary branch to stateful scoring
- [x] Make memory ingest inbound messages and feed planning/execution context
- **Status:** complete

### Phase 3: Wiring

- [x] Wire `dialogue / decision / memory` model routes into the runtime services
- [x] Ensure planning carries enough context for execution to generate grounded replies
- [x] Keep tool capabilities working while making tool replies conversational
- **Status:** complete

### Phase 4: Verification

- [x] Add deterministic tests for persona, interaction, dialogue, and memory-aware planning
- [x] Convert the relevant runtime/API tests away from `tmp_path` to in-memory support so they run here
- [x] Re-run lint, pytest, and compile verification
- **Status:** complete

### Phase 5: Delivery

- [x] Update planning logs with the new role-simulation behavior
- [x] Update runtime documentation for the new dialogue/persona/memory path
- **Status:** complete

## Key Questions

1. What changed in dialogue? Narrative and hybrid steps now generate actual reply text instead of returning a generic execution string.
2. What changed in cognition? Persona extraction, interaction choice, and memory summarization now all have real implementations with model-backed paths plus deterministic fallback.
3. What changed in testing? The main runtime/API tests now use in-memory storage helpers instead of `tmp_path`, so the suite can pass in this sandbox.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Keep model-backed behavior optional, with deterministic fallback | The runtime should work without live API keys, but still consume the configured model routes when available |
| Treat inbound user messages as active memory unless they are low-signal acknowledgements | This creates continuity without flooding memory with trivial noise |
| Make URL/search message steps `hybrid` | The tool result and the user-facing reply now happen in the same first action |
| Replace `tmp_path`-heavy tests with in-memory store harnesses | This removes environment-specific ACL failures and keeps verification local |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Sandbox ACLs blocked `tmp_path` and even project-local `--basetemp` cleanup | 1 | Reworked the relevant tests to use in-memory store harnesses instead of filesystem fixtures |
| Memory retrieval recency bonus initially surfaced unrelated active memories | 1 | Only apply the recency bonus after a real lexical match exists |

## Notes

- Planning and replan were already real before this slice; this work closed the remaining gap above them.
- The runtime now behaves like a minimal role simulation rather than a scheduling shell with placeholder text.
