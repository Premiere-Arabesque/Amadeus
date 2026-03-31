# Task Plan: Simplify Execution Zones to Real / Non-Real

## Goal
Refactor the runtime from the current three-zone execution model to a lighter two-zone model (`real` / `non_real`) in three controlled phases, while keeping the project runnable during the transition.

## Current Phase
Phase 2

## Phases

### Phase 1: Discovery, Mapping, and Compatibility Plan
- [x] Confirm user intent: collapse three execution zones into two
- [x] Map current zone-related code paths across runtime, core types, UI, and docs
- [x] Create file-based plan and findings for the migration
- [x] Define migration strategy that keeps the project working between phases
- **Status:** complete

### Phase 2: Runtime and Schema Refactor
- [x] Refactor `execution.py` from explicit three-zone branching to a two-zone model
- [x] Update shared types and models (`ExecutionZone`, outcomes, step hints, debug payloads)
- [ ] Keep objective classification programmatic where possible instead of model-self-reporting
- [x] Preserve traceability for "tool was used" vs "non-real continuation"
- **Status:** in_progress

### Phase 3: UI, Tooling, and Docs Cleanup
- [ ] Update debug pages and standalone executor lab to reflect the new model
- [ ] Remove or rewrite remaining three-zone wording in prompts and docs
- [ ] Verify runtime behavior, debug outputs, and planning/replan compatibility
- [ ] Summarize remaining cleanup opportunities if any legacy fields remain
- **Status:** pending

## Key Questions
1. Which current three-zone distinctions are true runtime semantics vs only presentation or prompt wording?
2. How should "real vs non-real" be represented during migration: replace `ExecutionZone` directly, or introduce a compatibility layer first?
3. Which code paths must remain objective/programmatic so the executor agent does not become overburdened?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use a three-phase migration instead of one big rewrite | Keeps the project runnable and reviewable while large runtime logic changes land |
| Treat `execution.py` as the main refactor hotspot | Most three-zone branching, fallback, and loop semantics currently live there |
| Keep the file-based plan as source of truth for this migration | User explicitly asked to use `planning-with-files` and ignore older md contents |
| Use compatibility parsing for old zone strings while moving shared types to `real` / `non_real` | Prevents old snapshots, prompt outputs, and debug payloads from breaking immediately |
| Remove explicit Weak Real / Ambiguity runtime branching before cleaning up docs and UI | Reduces semantic complexity at the runtime core first, then presentation can follow |
| Keep old request enum values in executor-lab temporarily, but map all non-real choices to `non_real` internally | Lets older debug requests keep working during the UI cleanup window |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| None yet | 1 | N/A |

## Notes
- The user wants a lighter model: `real` if a tool was actually called in that turn, otherwise `non_real`.
- The user does not want the executor itself to carry unnecessary classification burden.
- The user prefers staged change over all-at-once rewrite.
