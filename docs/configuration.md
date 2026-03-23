# Configuration

Amadeus reads local configuration from the project root `.env` file. The app loads it automatically at startup.

## Quick Start

1. Copy `.env.example` to `.env`
2. Fill in your model API keys
3. Fill in QQ credentials if you want QQ enabled
4. Start the server

## Runtime Address

These values control the local FastAPI address:

- `AMADEUS_HOST`
- `AMADEUS_PORT`

Defaults:

- `AMADEUS_HOST=127.0.0.1`
- `AMADEUS_PORT=8010`

## Model Routing

Amadeus separates model routing by workload:

- `dialogue`
  - user-facing conversation
- `decision`
  - replan, policy, branch decisions
- `memory`
  - memory extraction and organization

Environment variables:

- `AMADEUS_DIALOGUE_PROVIDER`
- `AMADEUS_DIALOGUE_MODEL`
- `AMADEUS_DIALOGUE_API_KEY_ENV`
- `AMADEUS_DIALOGUE_BASE_URL`
- `AMADEUS_DECISION_PROVIDER`
- `AMADEUS_DECISION_MODEL`
- `AMADEUS_DECISION_API_KEY_ENV`
- `AMADEUS_DECISION_BASE_URL`
- `AMADEUS_MEMORY_PROVIDER`
- `AMADEUS_MEMORY_MODEL`
- `AMADEUS_MEMORY_API_KEY_ENV`
- `AMADEUS_MEMORY_BASE_URL`

Supported providers today:

- `openai`
- `anthropic`

Provider key variables are separate, for example:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `DASHSCOPE_API_KEY`

Note:

- `*_API_KEY_ENV` should contain an environment variable name, not the raw key itself
- `*_BASE_URL` can be used for OpenAI-compatible gateways

## QQ Settings

QQ now runs in gateway long-connection mode. It does not need a public callback URL.

Required for QQ:

- `AMADEUS_QQ_ENABLED=true`
- `AMADEUS_QQ_APP_ID`
- `AMADEUS_QQ_APP_SECRET`

Optional:

- `AMADEUS_QQ_ACCESS_TOKEN_URL`
- `AMADEUS_QQ_API_BASE_URL`
- `AMADEUS_QQ_SANDBOX_API_BASE_URL`
- `AMADEUS_QQ_USE_SANDBOX`

Current behavior:

- Amadeus exchanges `App ID + App Secret` for an access token
- Amadeus fetches the QQ gateway URL and opens the WebSocket itself
- inbound QQ private messages arrive as `C2C_MESSAGE_CREATE`
- outbound replies are still passive QQ REST replies tied to the inbound `message_id`
- current scope is `QQ C2C private chat`

Implementation entrypoint:

- [app/communication/qq.py](c:/Users/lenovo/Desktop/agent/Amadeus/app/communication/qq.py)

## Code Entry Points

Main config-related files:

- [app/infra/env.py](c:/Users/lenovo/Desktop/agent/Amadeus/app/infra/env.py)
- [app/infra/settings.py](c:/Users/lenovo/Desktop/agent/Amadeus/app/infra/settings.py)
- [app/communication/qq.py](c:/Users/lenovo/Desktop/agent/Amadeus/app/communication/qq.py)
- [app/main.py](c:/Users/lenovo/Desktop/agent/Amadeus/app/main.py)

## Inspection APIs

These endpoints are transport-agnostic:

- `GET /api/runtime/state`
- `GET /api/memory?limit=10`
- `GET /api/memory/search?query=...&top_k=5`

Persisted files:

- `raw_logs/runtime.jsonl`
- `snapshots/runtime.jsonl`
- `memory/core_memory.json`
- `memory/active_memory.jsonl`
- `memory/archive_memory.jsonl`
