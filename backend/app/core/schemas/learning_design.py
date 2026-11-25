"""Validated course learning design and its server-owned revision."""

from typing import Literal, Self, get_args
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

RoleId = Literal[
    "evidence_researcher", "viewpoint_challenger", "feedback_prompter", "problem_progressor"
]
StageId = Literal[
    "problem_construction", "meaning_exploration", "explanation_integration", "application_solution"
]
DEFAULT_STAGE_NAMES: dict[StageId, str] = {
    "problem_construction": "问题构建",
    "meaning_exploration": "意义探索",
    "explanation_integration": "解释整合",
    "application_solution": "应用解决",
}


class RoleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: RoleId
    enabled: StrictBool
    prompt: str = Field(max_length=2500)


class StageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StageId
    name: str = Field(max_length=48)
    activity_guidance: str = Field(max_length=1600)


class LearningDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[RoleItem] = Field(max_length=4)
    orchestration_mode: Literal["adaptive", "single", "parallel", "debate", "pipeline"]
    stages: list[StageItem] = Field(max_length=4)

    @model_validator(mode="after")
    def validate_items(self) -> Self:
        if len({role.id for role in self.roles}) != len(self.roles):
            raise ValueError("Role IDs must be unique")
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError("Stage IDs must be unique")
        if len(self.roles) == 4 and not any(role.enabled for role in self.roles):
            raise ValueError("At least one role must be enabled")
        return self


class DesignSnapshot(LearningDesign):
    revision: str

    @classmethod
    def from_design(cls, design: LearningDesign) -> Self:
        roles = {role.id: role for role in design.roles}
        stages = {stage.id: stage for stage in design.stages}
        return cls(
            revision=uuid4().hex,
            orchestration_mode=design.orchestration_mode,
            roles=[
                roles[role_id] if role_id in roles else RoleItem(id=role_id, enabled=True, prompt="")
                for role_id in get_args(RoleId)
            ],
            stages=[
                StageItem(
                    id=stage_id,
                    name=(stages[stage_id].name.strip() if stage_id in stages else "") or default_name,
                    activity_guidance=stages[stage_id].activity_guidance if stage_id in stages else "",
                )
                for stage_id, default_name in DEFAULT_STAGE_NAMES.items()
            ],
        )

    def has_stage_overrides(self) -> bool:
        return any(
            stage.name != DEFAULT_STAGE_NAMES[stage.id] or stage.activity_guidance.strip()
            for stage in self.stages
        )


class LearningDesignResponse(BaseModel):
    design: DesignSnapshot | None


class ApplyLearningDesignResponse(BaseModel):
    project_id: str
    revision: str
