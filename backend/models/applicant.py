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
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: Optional[int] = Field(default=None, primary_key=True)
    applicant_id: int = Field(foreign_key="applicant.id")
    doc_type: str  # cv | sop | reference | portfolio
    filename: str
    file_path: str
    summary: str = ""  # AI-generated on upload
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
