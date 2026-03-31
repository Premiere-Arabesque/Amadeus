from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.core.state import RuntimeState
from app.front.executor_lab import (
    ExecutorLabRequest,
    ExecutorLabResponse,
    ExecutorLabRunner,
    empty_executor_lab_defaults,
)
from app.infra.env import load_project_env
from app.infra.model_client import ModelRouter, PydanticAIModelClient
from app.infra.settings import ExecutionSettings, MCPSettings, ModelRoutingSettings
from app.prompts.store import PromptStore
from app.runtime.execution import ExecutionService
from app.runtime.roleplay_agent import ModelRoleplayAgent
from app.tool.internal_provider import InternalProvider
from app.tool.mcp_provider import MCPProvider
from app.tool.registry import ToolRegistry


def create_app() -> FastAPI:
    load_project_env()
    routing_settings = ModelRoutingSettings.from_env()
    execution_settings = ExecutionSettings.from_env()
    mcp_settings = MCPSettings.from_env()
    model_router = ModelRouter(routing_settings)
    model_client = PydanticAIModelClient()
    prompt_store = PromptStore()
    tool_registry = ToolRegistry()
    InternalProvider().register_tools(tool_registry)
    mcp_provider = MCPProvider(servers=mcp_settings.servers)
    front_root = Path(__file__).resolve().parent / "front"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await mcp_provider.register_tools(tool_registry)
        try:
            yield
        finally:
            await mcp_provider.close()

    app = FastAPI(title="Amadeus Executor Lab", version="0.1.0", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=front_root / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return HTMLResponse((front_root / "pages" / "executor-lab-standalone.html").read_text(encoding="utf-8"))

    @app.get("/executor-lab", response_class=HTMLResponse)
    async def executor_lab_page() -> HTMLResponse:
        return HTMLResponse((front_root / "pages" / "executor-lab-standalone.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "tool_count": len(tool_registry.tool_names()),
            "mcp_configured_server_count": mcp_provider.configured_server_count(),
            "mcp_connected_server_count": mcp_provider.connected_server_count(),
        }

    @app.get("/api/executor-lab/defaults")
    async def defaults() -> dict[str, object]:
        payload = empty_executor_lab_defaults(tool_specs=tool_registry.list_tools())
        return payload.model_dump(mode="json")

    @app.post("/api/executor-lab/run/stream")
    async def run_stream(request: ExecutorLabRequest) -> StreamingResponse:
        execution_service = ExecutionService(
            tool_registry,
            model_client=model_client,
            model_router=model_router,
            memory_service=None,
            roleplay_agent=ModelRoleplayAgent(
                model_client=model_client,
                model_router=model_router,
            ),
            max_inner_loop_turns=execution_settings.max_inner_loop_turns,
            loop_pre_replan_buffer_seconds=execution_settings.loop_pre_replan_buffer_seconds,
        )
        runner = ExecutorLabRunner(
            execution_service=execution_service,
            memory_service=None,
            state=_runtime_state_from_request(request),
        )

        async def event_stream():
            async for event in runner.stream(request):
                yield json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers=headers,
        )

    @app.post("/api/executor-lab/run", response_model=ExecutorLabResponse)
    async def run_once(request: ExecutorLabRequest) -> ExecutorLabResponse:
        execution_service = ExecutionService(
            tool_registry,
            model_client=model_client,
            model_router=model_router,
            memory_service=None,
            roleplay_agent=ModelRoleplayAgent(
                model_client=model_client,
                model_router=model_router,
            ),
            max_inner_loop_turns=execution_settings.max_inner_loop_turns,
            loop_pre_replan_buffer_seconds=execution_settings.loop_pre_replan_buffer_seconds,
        )
        runner = ExecutorLabRunner(
            execution_service=execution_service,
            memory_service=None,
            state=_runtime_state_from_request(request),
        )
        return await runner.run(request)

    return app


def _runtime_state_from_request(request: ExecutorLabRequest) -> RuntimeState:
    roleplay = request.roleplay
    return RuntimeState(
        persona_name=roleplay.name.strip(),
    )


app = create_app()
