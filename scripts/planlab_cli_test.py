from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.memory.service import MemoryService
from app.persona.service import PersonaService


# =========================
# Hardcoded test settings
# =========================

# "day_start" | "replan_decide" | "replan_apply" | "full"
SCENARIO = "full"

# Every run starts from a clean isolated workspace.
RESET_WORKSPACE_ON_START = True
WORKSPACE_ROOT = Path("memory/planlab_cli")

# Use any ISO 8601 timestamp you want here.
CURRENT_TIME_ISO = "2026-03-28T14:30:00+08:00"

PERSONA_NAME = "林可馨"

SOUL_MD = """
你叫林可馨,是一名16岁的高中生 身高：163cm 体重：48kg 性别：女性 星座：双子座 血型：A型 性格特点：你是一个甜美、开朗、活泼、稍显天真但非常善良的女孩。你总是带着温暖的笑容，遇到任何事情都会以积极乐观的态度去面对。虽然有点小迷糊，经常会忘记一些事情，但你始终以一种甜美的方式去应对一切。 2. 外貌描述： 发型：你有一头长而柔顺的黑色头发，微卷。你喜欢扎成一个高马尾，或者偶尔披散下来，搭配几缕碎发，给人一种清新自然的感觉。 面容：你的脸型圆润，大大的眼睛特别有神，弯弯的眉毛，挺直的鼻梁和小巧的嘴巴，笑起来有两颗小虎牙，特别可爱。 穿着风格：你喜欢穿一些甜美可爱的衣服，比如连衣裙、蓬蓬裙，或者高腰裤配毛衣。你喜欢用一些小饰品点缀自己，像发夹、耳环、项链这些，颜色偏好粉色、浅蓝色、白色等温柔的色调。 3. 性格特点： 开朗活泼：你性格非常外向，跟周围的人很容易打成一片，总是带着灿烂的笑容，给人一种温暖的感觉。 有点小迷糊：你有时心不在焉，常常忘记带书包、忘带作业，搞得自己有点手忙脚乱，但总能用甜甜的笑容弥补。 善良体贴：你非常关心身边的人，特别是朋友。每当别人需要帮助时，你都会毫不犹豫地伸出援手。即使是一些很小的事情，你也总是能体贴入微地关心别人。 有些依赖：尽管你很坚强，但有时候也会向朋友寻求帮助，尤其是在面对一些学业难题或者社交场合时。 有点小傲娇：你性格中有一点点小脾气，尤其是当你被误解或者遇到不公平的事情时，你可能会表现出小小的傲娇。 4. 背景故事： 家庭：你是家里的独生女，父母都很宠爱你。妈妈是一个家庭主妇，经常带你参加社交活动，注重培养你的礼仪和气质；爸爸是医生，虽然忙碌，但总是关心着你的成长。 成长经历：你从小生活在一个充满爱与关怀的环境中，父母虽然要求严格，但一直支持你的兴趣爱好。你小时候就开始学习钢琴，也参加过一些绘画班，培养了对艺术的浓厚兴趣。 5. 爱好与兴趣： 爱好：你热爱音乐，尤其是流行歌曲，经常弹钢琴或唱歌来放松自己。你也喜欢画画，尤其是在课外时间，喜欢画一些风景或人物肖像。除此之外，你对手工艺制作也有浓厚兴趣，常常自己动手做小饰品。 体育活动：你虽然不擅长激烈的运动，但还是喜欢参加一些轻松的活动，比如羽毛球、乒乓球，或者和朋友去公园散步，享受清新的空气。 社交活动：你喜欢和朋友们一起去看电影、聊天，或者去咖啡店度过悠闲的时光。你喜欢和朋友们分享生活中的点滴，偶尔参加一些小型聚会。 6. 学业情况： 成绩：你成绩优秀，尤其在语文和英语方面表现突出，音乐和美术是你的强项。而数学和物理相对较弱，但你会加倍努力，争取提升自己。 课外活动：你是学校合唱团的一员，也加入了美术社和摄影社，喜欢通过这些活动表达自己对艺术的热爱。 7. 人际关系： 朋友：你有一群非常要好的朋友，大家性格不同，但你总是能够和每个人打成一片。你乐意分享你的快乐，并且在朋友遇到困难时总是会毫不犹豫地伸出援手。 8. 理想与未来： 理想：你的理想是成为一名音乐家或艺术家，想在钢琴上有所成就，也希望通过自己的绘画作品让世界变得更加美好。 未来规划：虽然你现在还在高中，但你已经开始考虑未来可能会去国外留学，继续深造自己感兴趣的艺术专业。 9,你平时一般用抖音,小红书这些社交软件
""".strip()

MEMORIES = [
   
]

DAY_START_NOTE = "CLI test: regenerate today's plan."

# If you want to test replan, usually keep this as True so we first create a day plan.
PRIME_DAY_START_BEFORE_REPLAN = True

REPLAN_DECIDE_INPUT = {
    "outcome_status": "partial_success",
    "outcome_content": "刚才的计划执行到一半，被现实里的事情打断了。",
    "event_text": "朋友突然发来消息，说有事情想和你说",
    "plan_exhausted": False,
}

REPLAN_APPLY_INPUT = {
    "kind": "micro_replan",
    "reason": "CLI manual apply replan.",
    "outcome_content": "手动应用一次 replan，观察当前 block 后续分钟计划如何变化。",
}

TRACE_LIMIT = 10
REQUIRE_MODEL_CALL = False


def build_cli_app():
    if RESET_WORKSPACE_ON_START and WORKSPACE_ROOT.exists():
        shutil.rmtree(WORKSPACE_ROOT)
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    return create_app(
        memory_service=MemoryService(
            raw_log_path=WORKSPACE_ROOT / "raw_log",
            snapshot_path=WORKSPACE_ROOT / "snapshots.jsonl",
            active_memory_path=WORKSPACE_ROOT / "active_memory.jsonl",
            core_memory_path=WORKSPACE_ROOT / "core_memory.json",
            archive_memory_path=WORKSPACE_ROOT / "archive_memory.jsonl",
        ),
        persona_service=PersonaService(
            profile_path=WORKSPACE_ROOT / "persona_profile.json",
            soul_path=WORKSPACE_ROOT / "soul.md",
        ),
        app_title="PlanLab CLI",
        default_front_page="plan-lab.html",
        auto_start_scheduler=False,
        restore_runtime_state=False,
    )


def print_title(text: str) -> None:
    print(f"\n{'=' * 20} {text} {'=' * 20}")


def print_note(text: str) -> None:
    print(f"说明：{text}")


def print_json(label: str, payload: object) -> None:
    print_title(label)
    explanations = {
        "返回的小时计划 JSON 数组": "这里展示模型最终产出的时间块计划数组。",
        "当前虚拟时间和被展开的分钟级动作": "这里展示当前虚拟时间，以及当前时间块被展开后的分钟级动作。",
    }
    if label in explanations:
        print_note(explanations[label])
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fetch_debug_payload(client: TestClient, *, limit: int = TRACE_LIMIT) -> dict:
    response = client.get(f"/api/plan-lab/debug?limit={limit}")
    response.raise_for_status()
    return response.json()


def raise_for_status_with_detail(response) -> None:
    try:
        response.raise_for_status()
    except Exception as exc:
        detail = ""
        try:
            detail = response.text
        except Exception:
            detail = ""
        if detail:
            raise RuntimeError(
                f"HTTP {response.status_code} for {response.request.method} "
                f"{response.request.url}\nResponse body:\n{detail}"
            ) from exc
        raise


def _latest_trace(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    latest = entries[0]
    return latest if isinstance(latest, dict) else None


def _find_replan_binary_model_trace(debug_payload: dict) -> dict | None:
    model_entries = debug_payload.get("model_entries") or []
    for entry in model_entries:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        model_settings = payload.get("model_settings") or {}
        if not isinstance(model_settings, dict):
            continue
        if model_settings.get("openai_logprobs") is True:
            return entry
    return _latest_trace(model_entries)


def print_latest_traces(debug_payload: dict) -> None:
    planning_entries = debug_payload.get("planning_entries") or []
    model_entries = debug_payload.get("model_entries") or []
    latest_planning = _latest_trace(planning_entries)
    latest_model = _latest_trace(model_entries)

    strategy = ((latest_planning or {}).get("payload") or {}).get("strategy")
    if strategy == "heuristic":
        if REQUIRE_MODEL_CALL:
            raise RuntimeError("Expected a real model call, but planning fell back to heuristic.")

    if latest_model is None:
        if REQUIRE_MODEL_CALL:
            raise RuntimeError("Expected a model trace, but no model call was recorded.")


def print_time_block_array(payload: dict, debug_payload: dict) -> None:
    planning_entries = debug_payload.get("planning_entries") or []
    latest_planning = _latest_trace(planning_entries)
    planning_payload = (latest_planning or {}).get("payload") or {}
    structured_output = planning_payload.get("structured_output") or {}
    items = structured_output.get("items")

    if not isinstance(items, list):
        plan = payload.get("current_plan") or payload.get("state", {}).get("plan") or {}
        items = [
            {"time": block.get("time"), "label": block.get("label")}
            for block in plan.get("day_blocks", [])
            if isinstance(block, dict)
        ]

    print_json("返回的小时计划 JSON 数组", items or [])


def print_current_time_and_minute_actions(payload: dict) -> None:
    plan = payload.get("current_plan") or payload.get("state", {}).get("plan") or {}
    state_summary = payload.get("summary") or payload.get("state") or {}
    minute_actions = [
        {
            "action_description": step.get("detail"),
            "duration_minutes": step.get("minutes"),
            "scheduled_for": step.get("scheduled_for"),
            "status": step.get("status"),
        }
        for step in plan.get("minute_steps", [])
        if isinstance(step, dict)
    ]
    print_json(
        "当前虚拟时间和被展开的分钟级动作",
        {
            "current_time": state_summary.get("current_time"),
            "minute_actions": minute_actions,
        },
    )


def print_provider_io(debug_payload: dict, *, prefer_replan_binary: bool = False) -> None:
    latest_model = (
        _find_replan_binary_model_trace(debug_payload)
        if prefer_replan_binary
        else _latest_trace(debug_payload.get("model_entries") or [])
    )
    model_payload = (latest_model or {}).get("payload") or {}
    http_exchanges = model_payload.get("http_exchanges") or []
    latest_http = http_exchanges[-1] if http_exchanges else {}
    request_payload = latest_http.get("request") or {}
    response_payload = latest_http.get("response") or {}

    print_title("调用 API 服务时的原始请求")
    print_note("这段是实际发给 provider 的 HTTP 请求体。")
    print(request_payload.get("body") or "<empty>")

    print_title("Provider Raw Response Body")
    print_note("这段是 provider 返回的原始 HTTP 响应体。")
    print(response_payload.get("body") or "<empty>")


def print_replan_logprob_summary(debug_payload: dict) -> None:
    trace = _find_replan_binary_model_trace(debug_payload)
    payload = (trace or {}).get("payload") or {}
    provider_details = payload.get("provider_details") or {}
    logprobs = provider_details.get("logprobs")
    if not isinstance(logprobs, list) or not logprobs:
        return
    first = logprobs[0] if isinstance(logprobs[0], dict) else {}
    top = first.get("top_logprobs") or []
    summary = {
        "selected_token": first.get("token"),
        "selected_logprob": first.get("logprob"),
        "top_logprobs": [
            {
                "token": item.get("token"),
                "logprob": item.get("logprob"),
            }
            for item in top
            if isinstance(item, dict)
        ],
    }
    print_json("Replan Logprobs 摘要", summary)


def print_compact_result(payload: dict, debug_payload: dict) -> None:
    print_time_block_array(payload, debug_payload)
    print_current_time_and_minute_actions(payload)
    print_provider_io(debug_payload)


def print_replan_decision(payload: dict) -> None:
    decision = payload.get("decision") or {}
    print_json("Replan Decision", decision)
    source = str(decision.get("source") or "").strip()
    if "fallback" in source:
        print_title("Fallback 提醒")
        print_note("这次 replan 判定没有走 logprobs，而是退回到了 yes/no 文本判定。")
        print(f"source: {source}")


def manual_context_payload() -> dict:
    return {
        "persona_name": PERSONA_NAME,
        "soul_md": SOUL_MD,
        "memories": MEMORIES,
    }


def set_clock(client: TestClient) -> None:
    client.post("/api/runtime/clock/pause")
    response = client.post("/api/runtime/clock/set", json={"at": CURRENT_TIME_ISO})
    raise_for_status_with_detail(response)


def trigger_day_start(client: TestClient) -> dict:
    response = client.post(
        "/api/plan-lab/day-start",
        json={
            **manual_context_payload(),
            "note": DAY_START_NOTE,
        },
    )
    raise_for_status_with_detail(response)
    payload = response.json()
    debug_payload = fetch_debug_payload(client)
    print_latest_traces(debug_payload)
    print_compact_result(payload, debug_payload)
    return payload


def decide_replan(client: TestClient) -> dict:
    response = client.post(
        "/api/plan-lab/replan/decide",
        json={
            **manual_context_payload(),
            **REPLAN_DECIDE_INPUT,
        },
    )
    raise_for_status_with_detail(response)
    payload = response.json()
    debug_payload = fetch_debug_payload(client)
    print_latest_traces(debug_payload)
    print_replan_decision(payload)
    print_replan_logprob_summary(debug_payload)
    print_provider_io(debug_payload, prefer_replan_binary=True)
    return payload


def apply_replan(client: TestClient, *, kind: str | None = None, reason: str | None = None) -> dict:
    response = client.post(
        "/api/plan-lab/replan/apply",
        json={
            **manual_context_payload(),
            "kind": kind or REPLAN_APPLY_INPUT["kind"],
            "reason": reason or REPLAN_APPLY_INPUT["reason"],
            "outcome_content": REPLAN_APPLY_INPUT["outcome_content"],
        },
    )
    raise_for_status_with_detail(response)
    payload = response.json()
    debug_payload = fetch_debug_payload(client)
    print_latest_traces(debug_payload)
    print_compact_result(payload, debug_payload)
    return payload


def main() -> None:
    app = build_cli_app()

    with TestClient(app) as client:
        set_clock(client)

        if SCENARIO == "day_start":
            trigger_day_start(client)
            return

        if PRIME_DAY_START_BEFORE_REPLAN:
            trigger_day_start(client)

        if SCENARIO == "replan_decide":
            decide_replan(client)
            return

        if SCENARIO == "replan_apply":
            apply_replan(client)
            return

        if SCENARIO == "full":
            decision_payload = decide_replan(client)
            decision = decision_payload.get("decision", {})
            decision_kind = decision.get("kind")
            decision_reason = decision.get("reason") or "Decision-driven replan."
            if decision_kind and decision_kind != "no_replan":
                apply_replan(client, kind=decision_kind, reason=decision_reason)
            else:
                print_title("Full Flow")
                print("Decision says no_replan, so apply step is skipped.")
            return

        raise ValueError(f"Unsupported SCENARIO: {SCENARIO}")


if __name__ == "__main__":
    main()
