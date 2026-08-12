"""Transport envelope for idempotent Schedule snapshot submission."""

from pydantic import BaseModel, ConfigDict, Field

from .canonical_v2_2 import CanonicalScheduleV22


class ScheduleSnapshotSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    external_project_id: str = Field(min_length=1, max_length=256)
    external_snapshot_id: str = Field(min_length=1, max_length=256)
    external_revision: str = Field(min_length=1, max_length=256)
    snapshot: CanonicalScheduleV22
