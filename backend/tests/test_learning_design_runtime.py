"""Exercise stored teacher designs with the real graph and an offline model."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from minio import Minio

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import ai
    from app.services.agents import agent_service as agent_module

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableLambda

from app.core.schemas.ai import AIChatRequest
from app.services.agents.course_learning_design import resolve_course_learning_design
from app.services.agents.orchestration_planner import OrchestrationPlanner
from app.websocket.handlers import chat_handler

CUSTOM_SOURCE = "custom_0123456789abcdef"
CUSTOM_REVIEW = "custom_0000000000000002"


def configuration(mode):
    return {
        "current_stage": "meaning_exploration",
        "ai_scaffold_mode": "multi_agent",
        "learning_design": {
            "revision": "class-revision",
            "orchestration_mode": mode,
            "roles": [
                {"id": "evidence_researcher", "enabled": False, "prompt": "do not send this"},
                {"id": "viewpoint_challenger", "enabled": True, "prompt": "Check {source} assumptions"},
                {"id": "feedback_prompter", "enabled": False, "prompt": ""},
                {"id": "problem_progressor", "enabled": True, "prompt": "Clarify {task} boundaries"},
            ],
            "stages": [
                {"id": "meaning_exploration", "name": "Compare evidence", "activity_guidance": "Check {reference} quality."},
                {"id": "application_solution", "name": "Apply", "activity_guidance": "Not the active stage"},
            ],
        },
    }


def test_design_uses_published_group_stage_and_preserves_direct_mode():
    version = configuration("parallel")
    project = SimpleNamespace(id="group-a", experiment_version=version)
    request = AIChatRequest(
        project_id="group-a", message="hello", current_stage="application_solution",
        preferred_subagent="evidence_researcher",
    )
    graph = ai._configured_tutor_context(project, request, "student")
    assert graph["current_stage"] == "meaning_exploration"
    assert graph["teacher_stage_guidance"] == "Check {reference} quality."
    assert graph["enabled_subagents"] == ["viewpoint_challenger", "problem_progressor"]
    assert graph["preferred_subagent"] is None
    assert resolve_course_learning_design(version, "inquiry")["teacher_stage_name"] == "Compare evidence"

    other = SimpleNamespace(id="group-b", experiment_version={"ai_scaffold_mode": "multi_agent"})
    assert ai._configured_tutor_context(other, request, "student") == {}
    version["ai_scaffold_mode"] = "single_agent"
    assert ai._configured_tutor_context(project, request, "student") == {}


@pytest.mark.asyncio
async def test_tutor_rejects_conversation_from_another_group_or_user():
    for project_id, user_id in [("other-group", "student"), ("group-a", "other-user")]:
        conversation = SimpleNamespace(project_id=project_id, user_id=user_id)
        with patch.object(ai.AIConversation, "get", AsyncMock(return_value=conversation)):
            with pytest.raises(ai.HTTPException) as error:
                await ai._get_chat_conversation("conversation", "group-a", "student")
        assert error.value.status_code == 404


def test_specific_role_and_single_available_role_are_not_expanded():
    preferred = OrchestrationPlanner.plan(
        message="help", preferred_subagent="problem_progressor",
        enabled_subagents=["problem_progressor", "viewpoint_challenger"],
        teacher_orchestration_mode="parallel",
    )
    assert preferred["orchestration_mode"] == "single"
    assert preferred["active_agents"] == ["problem_progressor"]
    only = OrchestrationPlanner.plan(
        message="help", preferred_subagent="evidence_researcher",
        enabled_subagents=["problem_progressor"], teacher_orchestration_mode="debate",
    )
    assert only["orchestration_mode"] == "single"
    assert only["active_agents"] == ["problem_progressor"]


def test_custom_roles_follow_routing_anchor_and_respect_exact_preference():
    roles = {
        CUSTOM_SOURCE: {"name": "来源核验", "routing_anchor": "evidence_researcher"},
        CUSTOM_REVIEW: {"name": "出处复核", "routing_anchor": "evidence_researcher"},
    }
    choices = {
        "current_stage": "meaning_exploration",
        "enabled_subagents": [CUSTOM_SOURCE, CUSTOM_REVIEW, "evidence_researcher", "viewpoint_challenger"],
        "teacher_orchestration_mode": "parallel",
        "custom_roles": roles,
    }
    planned = OrchestrationPlanner.plan(message="需要补充资料和证据", **choices)
    assert planned["primary_subagent"] == CUSTOM_SOURCE
    assert "evidence_researcher" not in planned["active_agents"]
    assert "来源核验" in planned["agent_instructions"][CUSTOM_SOURCE]
    assert planned["orchestration_mode"] == "parallel"

    mentioned = OrchestrationPlanner.plan(message="请 @出处复核：分析这条记录", **choices)
    assert mentioned["active_agents"] == [CUSTOM_REVIEW]
    assert mentioned["orchestration_mode"] == "single"
    unmatched = OrchestrationPlanner.plan(message="@出处复核员 讨论新材料", **choices)
    assert unmatched["orchestration_mode"] == "parallel"

    service = agent_module.AgentService()
    rag = service._resolve_stage_aware_rag_plan(
        plan=mentioned, context={"teacher_custom_roles": roles},
    )
    assert rag["should_retrieve"] and "resource" in rag["source_types"]


@pytest.mark.asyncio
async def test_tutor_rest_fallback_receives_the_same_teacher_graph():
    version = configuration("pipeline")
    version["collaboration_memory_enabled"] = False
    project = SimpleNamespace(id="group-a", experiment_version=version)
    request = AIChatRequest(project_id="group-a", message="hello", use_rag=False, selected_text="selected {passage}")
    with patch.object(ai.Project, "get", AsyncMock(return_value=project)), patch.object(
        ai, "ensure_project_access", AsyncMock(),
    ), patch.object(
        ai.group_memory_service, "get_project_task_context", AsyncMock(return_value="task"),
    ), patch.object(
        ai.ai_service, "chat", AsyncMock(return_value={"conversation_id": "conversation", "message": "answer"}),
    ) as fallback:
        await ai.chat.__wrapped__(request, None, SimpleNamespace(id="student"))
    graph = fallback.await_args.kwargs["graph_context"]
    assert graph["teacher_orchestration_mode"] == "pipeline"
    assert graph["learning_design_revision"] == "class-revision"
    assert graph["teacher_role_prompts"]["viewpoint_challenger"] == "Check {source} assumptions"
    assert "selected {passage}" in fallback.await_args.kwargs["context"]["learner_selection_context"]


@pytest.mark.asyncio
async def test_group_uses_published_design_and_not_stale_stage():
    from app.repositories import chat_log
    from app.services.activity_service import activity_service

    version = configuration("debate")
    version["collaboration_memory_enabled"] = False
    project = SimpleNamespace(id="group-a", experiment_version=version)
    graph_contexts = []

    async def stream(**kwargs):
        graph_contexts.append(kwargs["context"])
        yield "answer"

    with patch.object(ai.Project, "get", AsyncMock(return_value=project)), patch.object(
        chat_handler, "_build_recent_group_chat_context", AsyncMock(return_value={}),
    ), patch.object(
        ai.group_memory_service, "get_project_task_context", AsyncMock(return_value="task"),
    ), patch.object(
        activity_service, "log_activity", AsyncMock(),
    ), patch.object(
        ai.agent_service, "chat_stream", stream,
    ), patch.object(
        chat_log, "ChatLog",
        return_value=SimpleNamespace(id="reply", insert=AsyncMock()),
    ):
        await chat_handler._process_ai_reply(
            SimpleNamespace(emit=AsyncMock()),
            "project:group-a", "group-a", "question", "session",
            routing_context={"current_stage": "application_solution", "enabled_subagents": ["evidence_researcher"]},
        )
    [graph] = graph_contexts
    assert graph["current_stage"] == "meaning_exploration"
    assert graph["teacher_stage_guidance"] == "Check {reference} quality."
    assert graph["teacher_orchestration_mode"] == "debate"
    assert graph["enabled_subagents"] == ["viewpoint_challenger", "problem_progressor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,runtime", [
    ("single", "langgraph_stategraph_sequence"),
    ("parallel", "langgraph_stategraph_send"),
    ("debate", "langgraph_stategraph_sequence"),
    ("pipeline", "langgraph_stategraph_sequence"),
])
async def test_graph_executes_configured_mode_and_treats_braces_as_text(mode, runtime):
    version = configuration(mode)
    context = {
        "project_id": "group-a",
        "current_stage": version["current_stage"],
        "ai_scaffold_mode": "multi_agent",
        "graph_version": "legacy-graph",
        "use_rag": False,
        "learner_selection_context": "selected {passage}",
        **resolve_course_learning_design(version, version["current_stage"]),
    }
    service = agent_module.AgentService()
    service.graph = object()
    prompts = []

    def offline_model(prompt):
        system = prompt.to_messages()[0]
        assert isinstance(system, SystemMessage)
        prompts.append(system.content)
        return SimpleNamespace(content="A prompt-based answer")

    with patch.object(agent_module, "resolve_role_model_id", AsyncMock(return_value=None)), patch.object(
        agent_module, "get_llm", AsyncMock(return_value=RunnableLambda(offline_model)),
    ), patch.object(
        agent_module, "guarded_ainvoke", AsyncMock(return_value=SimpleNamespace(content="A synthesized answer")),
    ) as synthesis, patch.object(
        service, "_record_graph_routing_event", AsyncMock(),
    ) as audit:
        events = [event async for event in service.chat_stream(
            persona_key="default-tutor", message="Please review evidence", session_id="conversation", context=context,
        )]

    final = next(event for event in events if event.get("type") == "done")
    decision = final["routing_decision"]
    assert decision["orchestration_mode"] == mode
    assert set(decision["active_agents"]) <= set(context["enabled_subagents"])
    assert audit.await_args.kwargs["graph_runtime"] == runtime
    assert len(prompts) == (1 if mode == "single" else 2)
    assert synthesis.await_count == (0 if mode == "single" else 1)
    assert all("Check {reference} quality." in prompt and "do not send this" not in prompt for prompt in prompts)
    assert all("selected {passage}" in prompt for prompt in prompts)
    assert any("Check {source} assumptions" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_custom_only_graphs_do_not_share_role_name_or_prompt_between_groups():
    service = agent_module.AgentService()
    service.graph = object()

    async def run(group, display, instruction):
        version = configuration("single")
        for builtin in version["learning_design"]["roles"]:
            builtin["enabled"] = False
        version["learning_design"]["roles"].append({
            "id": CUSTOM_SOURCE,
            "name": display,
            "routing_anchor": "evidence_researcher",
            "enabled": True,
            "prompt": instruction,
        })
        version["learning_design"]["roles"].append({
            "id": CUSTOM_REVIEW,
            "name": "Disabled role",
            "routing_anchor": "feedback_prompter",
            "enabled": False,
            "prompt": "Disabled private prompt",
        })
        context = {
            "project_id": group,
            "current_stage": version["current_stage"],
            "ai_scaffold_mode": "multi_agent",
            "use_rag": False,
            **resolve_course_learning_design(version, version["current_stage"]),
        }
        events = [event async for event in service.chat_stream(
            persona_key="default-tutor", message=group, session_id=group, context=context,
        )]
        return next(event for event in events if event.get("type") == "done")

    received = {}

    def offline_model(prompt):
        system, user = prompt.to_messages()
        received[user.content] = system.content
        return SimpleNamespace(content="A prompt-based answer")

    with patch.object(agent_module, "resolve_role_model_id", AsyncMock(return_value=None)) as model, patch.object(
        agent_module, "get_llm", AsyncMock(return_value=RunnableLambda(offline_model)),
    ), patch.object(service, "_record_graph_routing_event", AsyncMock()):
        first, second = await asyncio.gather(
            run("group-a", "史料核验员", "Protocol A {citation}"),
            run("group-b", "证据校对员", "Protocol B {citation}"),
        )
    assert first["ai_meta"]["primary_agent"] == "史料核验员"
    assert second["ai_meta"]["primary_agent"] == "证据校对员"
    assert first["routing_decision"]["active_agents"] == second["routing_decision"]["active_agents"] == [CUSTOM_SOURCE]
    assert "Protocol A {citation}" in received["group-a"] and "Protocol B" not in received["group-a"]
    assert "Protocol B {citation}" in received["group-b"] and "Protocol A" not in received["group-b"]
    assert all("Disabled private prompt" not in text for text in received.values())
    assert [call.args[0] for call in model.await_args_list] == ["evidence_researcher", "evidence_researcher"]
