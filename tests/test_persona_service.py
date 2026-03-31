import pytest

from tests.test_support import PersonaHarness, build_in_memory_persona_service


@pytest.mark.anyio
async def test_bootstrap_persists_soul_markdown() -> None:
    service, harness = build_in_memory_persona_service()

    profile = await service.bootstrap_from_text(
        "A sharp, curious researcher who likes quiet routines and careful planning.",
        name="Kurisu",
    )

    soul_markdown = harness.soul_store.payload

    assert profile.name == "Kurisu"
    assert soul_markdown is not None
    assert soul_markdown.startswith("# 灵魂档案：Kurisu")
    assert "## 核心设定" in soul_markdown
    assert "careful planning" in soul_markdown
    assert service.summary.startswith("A sharp, curious researcher")


@pytest.mark.anyio
async def test_persona_service_restores_profile_from_soul_only() -> None:
    harness = PersonaHarness()
    service, _ = build_in_memory_persona_service(harness=harness)

    await service.bootstrap_from_text(
        "A sharp, curious researcher who likes quiet routines and careful planning.",
        name="Kurisu",
    )

    restored_service, _ = build_in_memory_persona_service(harness=harness)
    restored_profile = restored_service.profile

    assert restored_profile is not None
    assert restored_profile.name == "Kurisu"
    assert restored_service.summary.startswith("A sharp, curious researcher")


def test_persona_service_rename_updates_soul_title() -> None:
    harness = PersonaHarness()
    harness.soul_store.payload = "# 灵魂档案：Kurisu\n\n## 核心设定\nA careful researcher.\n"
    service, _ = build_in_memory_persona_service(harness=harness)

    profile = service.rename("Mayuri")

    assert profile.name == "Mayuri"
    assert harness.soul_store.payload.startswith("# 灵魂档案：Mayuri")
    assert "A careful researcher." in harness.soul_store.payload


def test_replace_soul_markdown_uses_title_name() -> None:
    service, _ = build_in_memory_persona_service()

    profile = service.replace_soul_markdown(
        "# 灵魂档案：Kurisu\n\n## 核心设定\nA sharper, more assertive researcher persona.\n"
    )

    assert profile.name == "Kurisu"
    assert service.summary == "A sharper, more assertive researcher persona."
