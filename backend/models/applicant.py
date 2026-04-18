from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Applicant(SQLModel, table=True):
    __tablename__ = "applicant"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    field_of_study: str
    bio: str = ""
    preferred_language: str = "English"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_matched_at: Optional[datetime] = None   # when matching last ran for this applicant
    new_matches_count: int = 0                   # matches found since applicant last viewed


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: Optional[int] = Field(default=None, primary_key=True)
    applicant_id: int = Field(foreign_key="applicant.id")
    doc_type: str  # cv | sop | reference | portfolio
    filename: str
    file_path: str
    summary: str = ""  # AI-generated on upload
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class ChecklistItem(SQLModel, table=True):
    __tablename__ = "checklist_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    applicant_id: int = Field(foreign_key="applicant.id", index=True)
    text: str
    done: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
