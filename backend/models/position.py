from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Position(SQLModel, table=True):
    __tablename__ = "position"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id")
    title: str
    university: str = ""
    country: str = ""
    description: str = ""
    deadline: Optional[str] = None
    field: str = ""              # academic discipline, e.g. "Computer Science"
    apply_url: str = ""
    raw_html: str = ""
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
