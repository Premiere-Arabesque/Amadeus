# Findings & Decisions

## Requirements
- Collapse the current three-zone execution model into a simpler two-zone model.
- Keep runtime judgment lightweight: objective distinction should come from whether a tool was actually called.
- Avoid pushing too much classification responsibility into the executor agent.
- Perform the migration in three phases.
- Use file-based planning (`task_plan.md`, `findings.md`, `progress.md`) as working memory.

## Research Findings
- Three-zone logic is concentrated most heavily in `app/runtime/execution.py`.
- Shared low-level zone types currently live in `app/core/types.py` and are threaded into state/outcome models.
- The debug surfaces (`app/front/executor_lab.py`, standalone lab, and older lab UI) expose three-zone assumptions directly.
- The main executor runtime can already operate as two-zone after collapsing real failures and no-capability starts into `non_real`; the largest remaining code debt is now legacy wording and dead ambiguity helper code.
- Prompt wording outside README has been mostly updated to `non_real`; the unreachable ambiguity helper block in `app/runtime/execution.py` has now been removed.
- The main remaining legacy surface is compatibility handling for older zone values in shared types and debug request enums.
- The old text-based capability router has now been removed from the main execution path; fallback behavior is explicit-capability only unless the SDK executor-agent path is available.
- The old model-based internal responder / draft-generator paths have now also been removed from `app/runtime/execution.py`; only the executor-agent prompt path remains there.
- `ExecutionService` no longer consumes `PromptStore`; prompt-store plumbing is still used elsewhere in planning/replan/persona, but not in execution anymore.
- Heuristic execution fallback has now been removed as well: when executor-agent output is unavailable or the internal loop would need a fake roleplay responder, `execution.py` raises explicit runtime errors instead of inventing narrative continuation.
- The executor-agent prompt is clearer when treated as: short runtime context first, then the roleplay agent's latest natural-language utterance as the final/primary input block.
- Core memory writeback was previously based on `summarize_outcome()` compression; it now prefers a dialogue-style rendering from `outcome.execution_trace` when such trace data exists.
- A stable shape for roleplay context is easier if it uses few fixed fields (`soul_md`, `plan_context`) plus an append-only list of typed context blocks, rather than many brittle per-feature fields.
- Rendering should stay human-editable: `RoleplayAgentContext` now renders through a few small `f\"\"\"...\"\"\"` template methods so prompt wording can be changed in one file without touching storage shape.
- The main runtime now has a concrete roleplay-agent boundary: first executor input comes from `step.detail`, while later turns come from a `RoleplayAgent` fed by `RoleplayAgentContext.render_for_roleplay()`.
- Dialogue-style memory writeback is most reliable when execution traces carry the initial roleplay utterance explicitly (`roleplay_initial`) instead of inferring it later.
- The executor debug page should not keep its own separate roleplay prompt/loop if the goal is execution-only debugging; it is cleaner to hand-fill `RoleplayAgentContext` but still run the actual `ExecutionService` / `RoleplayAgent` path.
- README and other explanatory docs still describe the full Real / Weak Real / Ambiguity model.
- `planning.py` and `replan.py` are touched by zone-related context and wording, but they are not the primary implementation hotspot.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Start with impact mapping before touching runtime code | The zone model is used across core types, execution, UI, and docs |
| Migrate in phases instead of deleting zone concepts all at once | Reduces breakage risk and keeps the repo understandable during refactor |
| Preserve objective "tool used or not" semantics outside the executor's subjective output | Matches the user's desire to keep executor burden low |
| Parse legacy zone strings into the new two-zone model instead of trusting all producers to update immediately | Old prompt files, snapshots, and debug payloads may still emit three-zone values during migration |
| Refactor runtime branching first, then clean prompts/docs/UI in a later pass | The runtime behavior is the real source of truth; wording cleanup should follow the implementation |
| Keep executor-lab backward-compatible at the request layer while emitting only `real` / `non_real` internally | Avoids breaking manual debug flows during the transition |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Existing planning markdown files were large and stale | Replaced them with a fresh file-based plan per user instruction |
| Prior experiments introduced multiple overlapping debug surfaces | Noted as part of Phase 3 cleanup rather than blocking Phase 1 |

## Resources
- `app/runtime/execution.py`
- `app/core/types.py`
- `app/core/state.py`
- `app/core/outcomes.py`
- `app/front/executor_lab.py`
- `app/front_lab_main.py`
- `README.md`

## Visual/Browser Findings
- None for this phase.
