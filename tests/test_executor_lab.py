from __future__ import annotations

from types import SimpleNamespace

from app.core.state import RuntimeState
from app.front.executor_lab import ExecutorLabRequest, ExecutorLabRoleplayConfig, ExecutorLabRunner
from app.runtime.contact_book import ContactBook


def _runner(*, contact_book: ContactBook | None = None) -> ExecutorLabRunner:
    execution_service = SimpleNamespace(
        loop_pre_replan_buffer_seconds=30,
        roleplay_agent=None,
    )
    return ExecutorLabRunner(
        execution_service=execution_service,
        memory_service=None,
        state=RuntimeState(persona_name="花梨"),
        contact_book=contact_book or ContactBook(),
    )


def test_executor_lab_seeds_contact_book_from_manual_lines() -> None:
    contact_book = ContactBook()
    runner = _runner(contact_book=contact_book)
    request = ExecutorLabRequest(
        title="测试动作",
        detail="测试细节",
        roleplay=ExecutorLabRoleplayConfig(
            registered_contacts="用户 | api | default-user\n真由理 | wechat | mayuri-001"
        ),
    )

    runner._seed_contact_book(request)

    contacts = contact_book.list_contacts()
    assert [contact.name for contact in contacts] == ["用户", "真由理"]
    assert contact_book.resolve("真由理").channel == "wechat"
    assert contact_book.resolve("真由理").recipient_id == "mayuri-001"


def test_executor_lab_builds_roleplay_context_from_manual_blocks() -> None:
    runner = _runner()
    request = ExecutorLabRequest(
        title="测试动作",
        detail="测试细节",
        roleplay=ExecutorLabRoleplayConfig(
            soul_md="你是一个温柔的角色。",
            plan_context="19:00-21:00 晚间放松",
            context_entries="你想起了一些事情：\n- 昨天收藏了长沙美食攻略\n\n刚刚你已经打开了搜索页面。",
            extra_instructions="回答口语一点。",
        ),
    )

    context = runner._build_roleplay_context(request)

    assert context.soul_md == "你是一个温柔的角色。"
    assert context.plan_context == "19:00-21:00 晚间放松"
    assert [entry.kind for entry in context.entries] == ["manual_context", "manual_context", "debug_instruction"]
    assert "调试说明：回答口语一点。" == context.entries[-1].content
