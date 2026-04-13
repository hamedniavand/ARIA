from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Source(SQLModel, table=True):
    __tablename__ = "source"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    label: str
    is_active: bool = True
    last_scraped_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
