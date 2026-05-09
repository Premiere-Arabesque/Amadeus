# Task Plan: Integrated Frontend Workspace

## Goal
Build a single integrated frontend with a sidebar and four sections: persona management, main workbench, chat, and settings, while keeping debug information available as folded panels instead of separate standalone debug pages.

## Current Phase
Phase 3

## Phases

### Phase 1: Audit Current Frontend + API Surface
- [x] Confirm which existing pages/assets can be reused from the standalone executor/planner labs
- [x] Confirm the current API surface for personas, runtime state, chat, time controls, memory, and tool status
- [x] Identify the smallest viable data flow for the first integrated chat page and workbench execution view
- **Status:** completed

### Phase 2: Build the Integrated Shell
- [x] Add a new primary front page with sidebar navigation
- [x] Add the four user-facing sections:
  - persona management
  - workbench
  - chat
  - settings
- [x] Keep debug details folded by default inside the relevant sections
- **Status:** completed

### Phase 3: Wire Live Data + Validate
- [x] Wire persona CRUD / activation / soul editing
- [x] Wire runtime status, virtual clock controls, and plan display
- [x] Wire chat sending and message-feed rendering
- [x] Wire tool / MCP status
- [x] Smoke-check page load and core interactions
- **Status:** completed

## Key Questions
1. Which standalone debug-page patterns are worth directly reusing, and which should stay isolated?
2. What is the smallest acceptable first-version chat feed shape without inventing a new conversation entity?
3. How much realtime behavior should come from polling versus a new product-facing stream endpoint?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use one integrated frontend instead of multiple separate product pages | Matches the desired product shape and reduces UI fragmentation |
| Keep the sidebar structure as `角色 / 工作台 / 聊天 / 设置` | This matches the intended user flow and mental model |
| Keep debug information folded inside each section instead of exposing a separate debug page | Preserves engineering visibility without making the main UI feel like a lab |
| First chat page version can start as a single message stream rather than a multi-thread conversation list | No dedicated conversation-thread API exists yet, and the user wants minimalism |
| First workbench version can use polling against existing runtime/debug APIs instead of requiring a new production SSE path immediately | Reuses current APIs and gets the integrated UI moving faster |
