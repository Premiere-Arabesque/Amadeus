# Amadeus

**A character-driven agent runtime for sustainable virtual life.**

Amadeus is not a chatbot. It is a runtime that takes a character description and produces a continuous, coherent life trajectory — planning each day, executing actions minute by minute, remembering experiences, and interacting with the real world through tool calls and messaging channels.

> Character description → Continuous life trajectory & interaction

The same engine can power a fictional character living their daily life or a digital twin of a real person — the only difference is the input.

**Users can define their relationship with the character and interact with them at any time.**

<p align="center">
<img src="./architecture.svg" alt="Amadeus Architecture" width="820"/>
</p>

## Demo

> 🎬 *Coming soon — a demo video showing the character naturally reaching out to the user during her daily life.*

## What Makes Amadeus Different

**Real-world interaction, not sandbox simulation.** The agent connects to real internet services through MCP — browsing social media, searching the web, reading articles, sending messages. When a tool exists, the agent uses it for real. When it doesn't, the system simulates gracefully. When a new tool is added later, actions automatically upgrade from simulation to real execution with zero changes to core logic.

**Character consistency is architecturally protected.** The roleplay agent only ever sees natural language — no JSON schemas, no tool definitions, no structured API responses. A separate executor sub-agent handles all tool routing and result packaging. This isn't a prompting trick; it's a structural guarantee that character immersion is never broken by system internals.

**Three-zone execution model.** Every action routes through one of three zones: *Real Zone* (tool exists → real execution), *Weak Real Zone* (no tool, but details can be inferred from persona → lightweight simulation), or *Ambiguity Zone* (insufficient information → detailed simulation with memory retrieval to maintain continuity). Tool failures degrade gracefully across zones. The upper layer never knows which zone handled the action.

**Persistent memory across four tiers.** Raw logs capture everything. Core memory stays in every prompt. Active memory (7–14 days) is retrievable via semantic vectors, BM25, and emotion vectors with reranking. Archive memory compresses older experiences for long-term recall. The runtime produces experiences; the memory system makes them retrievable.

**Layered planning without over-commitment.** No pre-generating a full day of minute-level schedules. Only the nearest time block expands into detail, and only the next 5–15 minutes become executable actions. Event-driven scheduling — no fixed-interval heartbeat polling. Replanning happens naturally after execution results or external interruptions.

**Character-driven intent, not hard-coded behavior.** The agent's relationships, routines, and preferences live in `soul.md`. If the character has a boyfriend/girlfriend, the agent might spontaneously decide to text him — not because a rule says so, but because the model naturally produces that intent from the character context. The executor catches the intent and routes it to a messaging tool.

## Architecture

The system is organized into four layers, with memory cutting across all of them:

- **Character Layer** — *Who this person is.* Generates `soul.md`: identity, traits, preferences, relationships, schedule, activity range.
- **Life Runtime Layer** — *How this person lives today.* Layered planning (day → hour → minute), three-zone execution with executor isolation, and logprobs-based replanning.
- **Interaction Layer** — *How this person connects with the world.* Converts external messages into runtime events, routes agent-initiated messages outward. Users can interrupt at any time.
- **Infrastructure Layer** — *How the system runs.* Runtime orchestrator, unified tool registry, MCP compatibility, multi-model routing (dialogue / decision / memory), storage.
- **Memory System** *(cross-cutting)* — Raw logs → active memory → archive memory, with core memory always in prompt. Retrieval: semantic + BM25 + emotion + reranker.

## Academic Context

Amadeus is a productization of the [Generative Agents](https://arxiv.org/abs/2304.03442) research direction:

| | Generative Agents | Amadeus |
|---|---|---|
| Environment | Closed sandbox | Real internet via MCP |
| Scope | Multi-agent social simulation | Single-agent persistent life |
| Goal | "Characters that move" | "Characters that live coherently" |
| Execution | Fully simulated | Real + simulated (three-zone) |
| Memory | Flat retrieval | Four-tier multi-signal retrieval |
| Interaction | User observes | User interrupts and influences |

## Current Status

🚧 **Alpha** — Core runtime loop is functional. Actively refining prompts, memory retrieval, and executor routing. Full open-source release planned for late April 2025.

Current scope: 1 character · 1 user · 1 messaging channel · complete runtime loop (persona → planning → execution → replanning → memory) · minimal MCP integration.

## Tech Stack

Python 3.12 · asyncio · FastAPI · Pydantic · PydanticAI · SQLAlchemy + SQLite · JSONL · MCP Python SDK

## Project Structure

```
Amadeus/
  app/
    main.py
    core/          # Events, state, outcomes, shared types
    persona/       # Character layer — soul.md generation
    runtime/       # Planning, execution, replanning, orchestration
    communication/ # Channels, message hub
    memory/        # Storage, retrieval, snapshots
    tool/          # Unified tool registry
    mcp/           # MCP compatibility layer
    infra/         # Model client, storage, logging
  docs/
  tests/
```

## Roadmap

- [ ] Multi-channel support (Telegram, QQ, Web)
- [ ] Advanced memory system with agentic retrieval
- [ ] Agent-to-agent interaction
- [ ] Voice and multimedia messaging

## License


AGPL-3.0 — see [LICENSE](./LICENSE) for details.
