"""Offline regression tests for legacy AI modes and memory isolation."""

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

from minio import Minio

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import ai, projects

from app.core.ai_scaffold_mode import normalize_ai_scaffold_mode
from app.services.activity_service import activity_service
from app.services.intervention_service import intervention_service
from app.services.learning_object_memory_service import is_experimental_memory_enabled
from app.services.memory_retrieval_service import memory_retrieval_service
from app.services.project_service import ProjectService
from app.services.research_config_service import ResearchConfigService
from app.services.research_event_service import research_event_service
from app.services.scaffold_round_memory_service import scaffold_round_memory_service
from app.websocket.handlers import chat_handler


MULTI = ("multi_agent", "multi", "multiagent", "multi_ai", " MULTI-AGENT ", " MULTI-AI ")
CONTROL = (None, "", "  ", "unknown", "single_agent", "single", "singleai", "single_ai", "general-ai")
REPLY = {"conversation_id": "conversation", "message": "reply"}


def project_with(mode):
    return SimpleNamespace(
        id="project",
        experiment_version={"ai_scaffold_mode": mode} if mode is not None else {},
        save=AsyncMock(),
    )


async def reply_stream(*args, **kwargs):
    yield "reply"


class ModeTests(TestCase):
    def test_runtime_consumers_agree_on_aliases_and_fallback(self):
        for mode in MULTI + CONTROL:
            with self.subTest(mode=mode):
                project = project_with(mode)
                version = project.experiment_version
                expected = "multi_agent" if mode in MULTI else "single_agent"
                self.assertEqual(normalize_ai_scaffold_mode(mode), expected)
                self.assertEqual(ai._ai_scaffold_mode(version), expected)
                self.assertEqual(chat_handler._extract_routing_context(project, "")["ai_scaffold_mode"], expected)
                self.assertEqual(is_experimental_memory_enabled(version), mode in MULTI)
                self.assertEqual(ai.agent_service._is_stage_aware_graph(version), mode in MULTI)

    def test_template_and_snapshot_use_canonical_mode_for_roles(self):
        for mode in (" MULTI-AI ", "general-ai", "unknown", None):
            with self.subTest(mode=mode):
                version = project_with(mode).experiment_version
                template = ResearchConfigService.build_experiment_version_from_template_config(
                    version, graph_version=None, template_source="test"
                )
                snapshot = ResearchConfigService.normalize_experiment_version_snapshot(version)
                for payload in (template, snapshot):
                    self.assertEqual(payload["ai_scaffold_mode"], normalize_ai_scaffold_mode(mode))
                    self.assertEqual(bool(payload["enabled_scaffold_roles"]), mode in MULTI)


class RuntimeTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(id="user")
        self.chat_data = ai.AIChatRequest(
            project_id="project", message="question", use_rag=False, conversation_id="conversation"
        )
        self.project_get = self.enterContext(patch.object(ai.Project, "get", new_callable=AsyncMock))
        self.enterContext(patch.object(ai, "ensure_project_access", new_callable=AsyncMock))
        self.enterContext(patch.multiple(
            ai.group_memory_service,
            get_project_task_context=AsyncMock(return_value="task brief"),
            get_stage_memory_context=AsyncMock(return_value={"content": "stage memory"}),
            refresh_and_get_group_state_context=AsyncMock(return_value={"content": "group state"}),
        ))

    async def test_stage_only_update_does_not_promote_missing_mode(self):
        for mode in (" MULTI-AI ", None, "unknown", "general-ai"):
            with self.subTest(mode=mode):
                project = project_with(mode)
                expected = normalize_ai_scaffold_mode(mode)
                self.assertEqual((await ProjectService.get_experiment_version(project))["ai_scaffold_mode"], expected)
                await ProjectService.update_experiment_version(project, {"current_stage": "problem_construction"})
                self.assertEqual(project.experiment_version["ai_scaffold_mode"], expected)

    async def test_tutor_chat_and_context_action(self):
        service = ai.ai_service
        for mode in (" MULTI-AI ", None, "unknown", "general-ai"):
            with self.subTest(mode=mode), patch.object(service, "chat", AsyncMock(return_value=REPLY)) as chat, \
                    patch.object(service, "raw_chat", AsyncMock(return_value=REPLY)) as raw:
                self.project_get.return_value = project_with(mode)
                for method in ("get_stage_memory_context", "refresh_and_get_group_state_context"):
                    getattr(ai.group_memory_service, method).reset_mock()
                await ai.chat.__wrapped__(self.chat_data, None, self.user)
                self.assertEqual(chat.await_count, int(mode in MULTI))
                self.assertEqual(raw.await_count, int(mode not in MULTI))
                action = ai.AIContextActionRequest(
                    project_id="project", action_type="summarize", context_type="document", content="text"
                )
                await ai.ai_action.__wrapped__(action, None, self.user)
                context = chat.await_args.kwargs["context"]
                self.assertEqual(context["project_task_context"], "task brief")
                self.assertEqual(context["stage_memory_context"], "stage memory" if mode in MULTI else None)
                self.assertEqual(context["group_state_context"], "group state" if mode in MULTI else None)
                for method in ("get_stage_memory_context", "refresh_and_get_group_state_context"):
                    self.assertEqual(getattr(ai.group_memory_service, method).await_count, 2 if mode in MULTI else 0)

    async def test_tutor_stream_passes_canonical_mode_to_graph(self):
        for mode in ("general-ai", " MULTI-AI "):
            with self.subTest(mode=mode):
                self.project_get.return_value = project_with(mode)
                conversation = SimpleNamespace(id="conversation", save=AsyncMock())
                with patch.object(ai.AIConversation, "get", AsyncMock(return_value=conversation)), \
                        patch.object(ai, "AIMessage") as message, \
                        patch.object(ai.ai_service, "raw_chat_stream", Mock(side_effect=reply_stream)) as raw, \
                        patch.object(ai.agent_service, "chat_stream", Mock(side_effect=reply_stream)) as graph:
                    message.find.return_value.count = AsyncMock(return_value=1)
                    message.return_value = SimpleNamespace(id="message", insert=AsyncMock())
                    response = await ai.chat_stream.__wrapped__(self.chat_data, None, self.user)
                    events = "".join([chunk async for chunk in response.body_iterator])
                self.assertIn("event: done", events)
                self.assertNotIn("event: error", events)
                if mode in MULTI:
                    raw.assert_not_called()
                    context = graph.call_args.kwargs["context"]
                    self.assertEqual(context["ai_scaffold_mode"], "multi_agent")
                    self.assertEqual(context["stage_memory_context"], "stage memory")
                else:
                    raw.assert_called_once()
                    graph.assert_not_called()
                    ai.group_memory_service.get_stage_memory_context.assert_not_awaited()
                    ai.group_memory_service.refresh_and_get_group_state_context.assert_not_awaited()

    async def test_group_reply_uses_current_mode_for_experimental_memory(self):
        for mode in (" MULTI-AI ", None, "general-ai", "unknown"):
            with self.subTest(mode=mode):
                self.project_get.return_value = project_with(mode)
                with patch.object(chat_handler, "_build_recent_group_chat_context", AsyncMock(return_value={})), \
                        patch("app.repositories.chat_log.ChatLog") as log, \
                        patch.object(activity_service, "log_activity", AsyncMock()) as activity, \
                        patch.object(ai.ai_service, "raw_completion_stream", Mock(side_effect=reply_stream)) as raw, \
                        patch.object(ai.agent_service, "chat_stream", Mock(side_effect=reply_stream)) as graph, \
                        patch.object(memory_retrieval_service, "build_experimental_memory_context",
                                     AsyncMock(return_value={"read_memory_ids": ["experimental"]})) as retrieve, \
                        patch.object(scaffold_round_memory_service, "record_ai_round", AsyncMock(return_value=None)) as record, \
                        patch.object(ai.group_memory_service, "maybe_refresh_stage_memory", AsyncMock()) as refresh_stage, \
                        patch.object(ai.group_memory_service, "maybe_refresh_group_state_memory", AsyncMock()) as refresh_state:
                    log.return_value = SimpleNamespace(id="reply", insert=AsyncMock())
                    await chat_handler._process_ai_reply(
                        Mock(emit=AsyncMock()), "project:project", "project", "question", "session",
                        routing_context={"ai_scaffold_mode": "multi_agent"},
                    )
                    # Let the two optional refresh tasks run before asserting.
                    await asyncio.sleep(0)
                self.assertEqual(log.call_args.kwargs["content"], "reply")
                self.assertEqual(raw.call_count, int(mode not in MULTI))
                self.assertEqual(graph.call_count, int(mode in MULTI))
                for operation in (retrieve, record, refresh_stage, refresh_state):
                    self.assertEqual(operation.await_count, int(mode in MULTI))
                self.assertEqual(activity.await_args.kwargs["metadata"]["read_memory_ids"],
                                 ["experimental"] if mode in MULTI else [])

    async def test_direct_memory_entry_and_stage_transition_are_guarded(self):
        for mode in (None, "general-ai", "unknown"):
            with self.subTest(mode=mode):
                project = project_with(mode)
                self.project_get.return_value = project
                result = await memory_retrieval_service.build_experimental_memory_context(
                    project=project, project_id="project", group_id="project:project",
                    stage_id="problem_construction", user_message="question",
                    experiment_version=project.experiment_version,
                )
                self.assertEqual(result["read_memory_ids"], [])
                self.assertEqual(result["stage_memory"], {})
                self.assertEqual(result["group_state"], {})
                with patch.object(ai.group_memory_service, "maybe_refresh_stage_memory", AsyncMock()) as refresh:
                    await projects.refresh_stage_memory_after_transition("project", "problem_construction")
                    refresh.assert_not_awaited()
        self.project_get.return_value = project_with(" MULTI-AI ")
        with patch.object(ai.group_memory_service, "maybe_refresh_stage_memory", AsyncMock()) as refresh:
            await projects.refresh_stage_memory_after_transition("project", "problem_construction")
            refresh.assert_awaited_once()

    async def test_shadow_prompt_only_goes_live_for_multi_agent(self):
        rule = next(iter(chat_handler.RULE_TYPE_TO_SUBAGENT))
        for mode in (" MULTI-AI ", "general-ai", None):
            with self.subTest(mode=mode):
                project = project_with(mode)
                project.experiment_version.update(mode="research", process_scaffold_mode="on")
                with patch.multiple(
                    research_event_service,
                    build_intervention_context_from_events=AsyncMock(return_value={}),
                    evaluate_shadow_prompt_policy=AsyncMock(return_value={"should_record": True, "would_send": True}),
                    record_batch_events=AsyncMock(),
                ), patch.multiple(
                    intervention_service,
                    check_interventions=AsyncMock(return_value=[{"rule_type": rule}]),
                    is_group_chat_live_enabled=Mock(return_value=True),
                ), patch.multiple(
                    chat_handler,
                    _count_online_learners=AsyncMock(return_value=2),
                    is_rule_eligible_for_live_group_prompt=Mock(return_value=(True, None)),
                    _emit_auto_group_prompt=AsyncMock(),
                ):
                    await chat_handler._evaluate_shadow_prompt_candidates(
                        sio=Mock(), project=project, room_id="project:project",
                        current_user_id="user", current_user_role="student",
                    )
                    event = research_event_service.record_batch_events.await_args.kwargs["events"][0]
                    expected = chat_handler.RULE_TYPE_TO_SUBAGENT[rule] if mode in MULTI else None
                    self.assertEqual(event["payload"]["recommended_subagent"], expected)
                    self.assertEqual(chat_handler._emit_auto_group_prompt.await_count, int(mode in MULTI))
