from typing import Optional
from datetime import datetime
from enum import Enum
from sqlmodel import SQLModel, Field


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    matched = "matched"
    preparing = "preparing"
    ready = "ready"
    submitted = "submitted"
    confirmed = "confirmed"
    error = "error"
    skipped = "skipped"


class Application(SQLModel, table=True):
    __tablename__ = "application"

    id: Optional[int] = Field(default=None, primary_key=True)
    position_id: int = Field(foreign_key="position.id")
    applicant_id: int = Field(foreign_key="applicant.id")
    match_score: float = 0.0
    priority_score: float = 0.0          # match_score × deadline urgency multiplier
    match_breakdown: str = ""            # JSON: {field_alignment, skills_match, research_fit, profile_strength}
    cover_letter: str = ""
    tailored_cv: str = ""                # AI-generated tailored CV text for this position
    status: ApplicationStatus = ApplicationStatus.discovered
    error_message: str = ""
    screenshot_path: str = ""
    submitted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
