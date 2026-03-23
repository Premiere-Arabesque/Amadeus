# Runtime Workflow

## Start Locally

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-server.ps1
```

Default local endpoints:

- `http://127.0.0.1:8010/health`
- `http://127.0.0.1:8010/api/persona`
- `http://127.0.0.1:8010/api/persona/bootstrap`
- `http://127.0.0.1:8010/api/messages`
- `http://127.0.0.1:8010/api/runtime/state`
- `http://127.0.0.1:8010/api/memory`
- `http://127.0.0.1:8010/api/memory/search`

Address overrides:

- `AMADEUS_HOST`
- `AMADEUS_PORT`

## Status

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\status-server.ps1
```

## Stop

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop-server.ps1
```

## Logs

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\tail-logs.ps1
```

Important runtime files:

- `logs/uvicorn.stdout.log`
- `logs/uvicorn.stderr.log`
- `runtime/uvicorn.pid`

Persisted runtime and memory files:

- `raw_logs/runtime.jsonl`
- `snapshots/runtime.jsonl`
- `memory/core_memory.json`
- `memory/active_memory.jsonl`
- `memory/archive_memory.jsonl`
- `memory/persona_profile.json`

## MVP Runtime Model

The current MVP runtime is schedule-driven, not heartbeat-driven.

- user messages interrupt immediately
- the scheduler wakes the runtime at the next planned minute-step time
- the scheduler also wakes the runtime at the next hour boundary to refresh the hour plan
- action completion is what triggers the next replan decision
- when a short window is exhausted or the previous step degrades, the runtime rewrites the next short window instead of leaving replan as a no-op
- idle periods may remain quiet in MVP

This means the project does not currently use a fixed-interval heartbeat as a general fallback loop.

## Current MCP Capabilities

The current MVP MCP capabilities are `read_url` and `search_web`.

- if an inbound user message contains an `http` or `https` URL, planning creates a `read_url` hybrid step
- if an inbound user message uses an explicit search trigger such as `search: ...`, `搜一下 ...`, or `查一下 ...`, planning creates a `search_web` hybrid step
- `read_url` fetches the page, extracts readable text, and returns a normalized outcome
- `search_web` currently uses the DuckDuckGo Instant Answer format to return structured search results
- hybrid execution means the runtime performs the tool call and turns the result into a user-facing reply in the same first action

Example:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8010/api/messages `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"user_id":"user-1","channel":"api","text":"Please read https://example.com/paper"}'
```

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8010/api/messages `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"user_id":"user-1","channel":"api","text":"search: quantum bananas"}'
```

## Inspect Runtime State

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8010/api/runtime/state
```

## Bootstrap Persona

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8010/api/persona/bootstrap `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"name":"Amadeus","seed_text":"A careful, curious persona with a steady daily rhythm."}'
```

## Inspect Persona

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8010/api/persona
```

## Current Role Simulation Behavior

The runtime now has a real MVP role-simulation layer above scheduling and MCP.

- persona bootstrap extracts structured summary, traits, relationship context, and preferences instead of only truncating the seed text
- inbound user messages are written into active memory unless they are low-signal acknowledgements
- planning reads recalled memory context back into new short-window steps
- narrative steps generate actual reply text instead of a generic execution string
- interaction policy can choose `respond_now` vs `record_only` instead of hardcoding every message to immediate reply
- emotion updates now change valence, arousal, and dominance continuously based on the last outcome
- the configured `dialogue / decision / memory` model routes are now wired into runtime services, with deterministic fallback if no live model route is configured

## Inspect Memory

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/memory?limit=10"
```

## Search Memory

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8010/api/memory/search?query=hello%20amadeus&top_k=5"
```

## QQ Runtime Mode

QQ no longer depends on a public callback URL.

When QQ is enabled:

- Amadeus starts a background QQ gateway connection during app startup
- incoming private messages arrive over the QQ gateway WebSocket
- outgoing replies are sent through QQ passive reply REST calls

Required QQ env values:

- `AMADEUS_QQ_ENABLED=true`
- `AMADEUS_QQ_APP_ID`
- `AMADEUS_QQ_APP_SECRET`
