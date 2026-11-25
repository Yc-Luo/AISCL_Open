"""Translate a stored class design into a request-scoped graph configuration."""

from typing import Any

from app.services.agents.multi_agent_roles import SUBAGENT_LABELS
from app.services.agents.multi_agent_stage_matrix import STAGE_ALIASES
from app.services.research_config_service import research_config_service


def resolve_course_learning_design(version: dict[str, Any], current_stage: str | None) -> dict[str, Any]:
    """Use only the design snapshot stored on this group, never client input."""
    design = version.get("learning_design")
    if not isinstance(design, dict) or not design.get("revision"):
        return {}

    overrides = {item["id"]: item for item in design.get("roles", [])}
    enabled = [role for role in SUBAGENT_LABELS if overrides.get(role, {}).get("enabled", True)]
    prompts = {
        role: overrides[role]["prompt"] for role in enabled
        if overrides.get(role, {}).get("prompt")
    }
    stage_id = STAGE_ALIASES.get(current_stage or "") or research_config_service.normalize_stage_id(current_stage)
    stage = next((item for item in design.get("stages", []) if item["id"] == stage_id), {})
    return {
        "learning_design_revision": design["revision"],
        "enabled_subagents": enabled,
        "teacher_role_prompts": prompts,
        "teacher_orchestration_mode": design.get("orchestration_mode"),
        "teacher_stage_name": stage.get("name"),
        "teacher_stage_guidance": stage.get("activity_guidance"),
    }
