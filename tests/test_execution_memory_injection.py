from unittest.mock import ANY

import pytest

from app.core.outcomes import ExecutionTraceEntry
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionZone
from app.runtime.execution import ExecutionLoopTurn, ExecutionService, NextExecutorTurnResult
from app.runtime.roleplay_agent import RoleplayAgent
from app.runtime.roleplay_context import RoleplayAgentContext


class DummyToolRegistry:
    def list_tools(self):
        return []


class RecordingMemoryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.saved_contexts: list[str] = []

    def build_roleplay_agent_context(self, *, state):
        del state
        return RoleplayAgentContext(
            soul_md="Test soul",
            plan_context="14:00-15:00 Debug execution",
        )

    async def retrieve_and_inject_memories(
        self,
        *,
        query_text: str,
        context: RoleplayAgentContext,
        roleplay_name: str,
        top_k: int = 3,
        source: str,
        metadata=None,
        **kwargs,
    ):
        del kwargs, top_k
        self.calls.append(
            {
                "query_text": query_text,
                "roleplay_name": roleplay_name,
                "source": source,
                "metadata": dict(metadata or {}),
            }
        )
        return context.add_retrieved_memories(
            [f"memory related to {query_text}"],
            heading=f"{roleplay_name} recollects:",
            metadata=metadata,
        )

    def save_roleplay_agent_context(self, context: RoleplayAgentContext) -> RoleplayAgentContext:
        rendered = context.render_for_roleplay()
        self.saved_contexts.append(rendered)
        return context.model_copy(deep=True)


class CapturingRoleplayAgent(RoleplayAgent):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.context_snapshots: list[str] = []

    async def respond(self, *, context, step, state, event, scene, result, turn_index) -> str:
        del step, state, event, scene, result, turn_index
        self.context_snapshots.append(context.render_for_roleplay())
        return self.responses.pop(0) if self.responses else ""

    async def respond_to_interaction(self, **kwargs) -> str:
        raise AssertionError("Interaction path is not used in these execution tests.")


@pytest.mark.anyio
async def test_execution_injects_memories_before_first_roleplay_response() -> None:
    memory_service = RecordingMemoryService()
    roleplay_agent = CapturingRoleplayAgent([""])
    service = ExecutionService(
        DummyToolRegistry(),
        memory_service=memory_service,
        roleplay_agent=roleplay_agent,
        max_inner_loop_turns=1,
    )

    await service._continue_agent_executor_loop(
        step=PlanStep(title="Browse Xiaohongshu", detail="Open the app and inspect the feed."),
        state=RuntimeState(persona_name="Kurisu"),
        event=None,
        zone=ExecutionZone.REAL,
        scene="You opened Xiaohongshu and the recommendation feed started scrolling.",
        result="You saw several fashion and cafe recommendations.",
        execution_trace=[
            ExecutionTraceEntry(
                stage="scene",
                content="You opened Xiaohongshu and the recommendation feed started scrolling.",
            ),
            ExecutionTraceEntry(
                stage="result",
                content="You saw several fashion and cafe recommendations.",
            ),
        ],
        raw_data={},
        tool_invocations=[],
        initial_roleplay_message="Open the app and inspect the feed.",
        loop_context=None,
    )

    assert memory_service.calls == [
        {
            "query_text": "You opened Xiaohongshu and the recommendation feed started scrolling.",
            "roleplay_name": "Kurisu",
            "source": "execution",
            "metadata": {
                "step_id": ANY,
                "step_title": "Browse Xiaohongshu",
                "turn": 0,
                "query_source": "scene",
            },
        }
    ]
    assert "Kurisu recollects:" in roleplay_agent.context_snapshots[0]
    assert (
        "memory related to You opened Xiaohongshu and the recommendation feed started scrolling."
        in roleplay_agent.context_snapshots[0]
    )
    assert memory_service.saved_contexts
    assert "You opened Xiaohongshu and the recommendation feed started scrolling." in (
        memory_service.saved_contexts[0]
    )


@pytest.mark.anyio
async def test_execution_injects_memories_after_each_executor_turn() -> None:
    memory_service = RecordingMemoryService()
    roleplay_agent = CapturingRoleplayAgent(["Open the fashion post", ""])
    service = ExecutionService(
        DummyToolRegistry(),
        memory_service=memory_service,
        roleplay_agent=roleplay_agent,
        max_inner_loop_turns=2,
    )

    async def fake_next_loop_executor_turn(**kwargs):
        del kwargs
        return NextExecutorTurnResult(
            next_turn=ExecutionLoopTurn(
                zone=ExecutionZone.REAL,
                scene="You opened the fashion post and a caramel coat close-up filled the screen.",
                result="The post listed three outfit combinations built around the caramel coat.",
            )
        )

    service._next_loop_executor_turn = fake_next_loop_executor_turn  # type: ignore[method-assign]

    await service._continue_agent_executor_loop(
        step=PlanStep(title="Browse Xiaohongshu", detail="Open the app and inspect the feed."),
        state=RuntimeState(persona_name="Kurisu"),
        event=None,
        zone=ExecutionZone.REAL,
        scene="You opened Xiaohongshu and the recommendation feed started scrolling.",
        result="You saw several fashion and cafe recommendations.",
        execution_trace=[
            ExecutionTraceEntry(
                stage="scene",
                content="You opened Xiaohongshu and the recommendation feed started scrolling.",
            ),
            ExecutionTraceEntry(
                stage="result",
                content="You saw several fashion and cafe recommendations.",
            ),
        ],
        raw_data={},
        tool_invocations=[],
        initial_roleplay_message="Open the app and inspect the feed.",
        loop_context=None,
    )

    assert len(memory_service.calls) == 2
    assert memory_service.calls[0]["metadata"]["turn"] == 0
    assert memory_service.calls[1]["query_text"] == (
        "You opened the fashion post and a caramel coat close-up filled the screen."
    )
    assert memory_service.calls[1]["metadata"]["turn"] == 1
    assert "You opened the fashion post and a caramel coat close-up filled the screen." in (
        roleplay_agent.context_snapshots[1]
    )
    assert "Kurisu recollects:" in roleplay_agent.context_snapshots[1]
    assert len(memory_service.saved_contexts) == 2


def test_executor_prompt_includes_full_history_block() -> None:
    service = ExecutionService(DummyToolRegistry(), memory_service=None, roleplay_agent=None)

    prompt = service._executor_agent_prompt(
        step=PlanStep(title="刷小红书", detail="打开首页继续往下刷"),
        state=RuntimeState(persona_name="Kurisu"),
        event=None,
        current_scene="你正在浏览推荐流。",
        current_result="你看到了穿搭和美食帖子。",
        agent_response="把穿搭那个点开看看。",
        history=[
            {
                "roleplay_message": "打开小红书看看。",
                "tool_calls": [
                    {
                        "capability": "open_xhs",
                        "arguments": {"tab": "home"},
                        "detail": "Opened Xiaohongshu home feed.",
                        "status": "success",
                    }
                ],
                "events": [
                    {
                        "event_kind": "part_start",
                        "part_kind": "text",
                        "content": "先打开小红书首页。",
                    },
                    {
                        "event_kind": "function_tool_call",
                        "tool_name": "open_xhs",
                        "args": {"tab": "home"},
                    },
                    {
                        "event_kind": "function_tool_result",
                        "tool_name": "open_xhs",
                        "content": "Opened Xiaohongshu home feed.",
                    },
                ],
                "scene": "你打开了小红书首页。",
                "result": "推荐流里出现了几条穿搭和美食帖子。",
                "stop": False,
            }
        ],
    )

    assert "到目前为止的完整双 loop 历史" in prompt
    assert "Roleplay 回复：打开小红书看看。" in prompt
    assert "Executor 调用工具" in prompt
    assert "open_xhs" in prompt
    assert "Executor 原始事件流" in prompt
    assert "function_tool_result" in prompt
    assert "scene：你打开了小红书首页。" in prompt
    assert "result：推荐流里出现了几条穿搭和美食帖子。" in prompt


def test_executor_history_filters_thinking_events() -> None:
    service = ExecutionService(DummyToolRegistry(), memory_service=None, roleplay_agent=None)

    assert service._should_persist_executor_event(
        {"event_kind": "part_delta", "part_delta_kind": "thinking", "content_delta": "..."}
    ) is False
    assert service._should_persist_executor_event(
        {"event_kind": "part_start", "part_kind": "thinking", "content": "..."}
    ) is False
    assert service._should_persist_executor_event(
        {"event_kind": "function_tool_call", "tool_name": "search_web"}
    ) is True
