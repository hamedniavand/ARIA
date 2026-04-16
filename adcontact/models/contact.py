from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class AppConfig(SQLModel, table=True):
    """Single-row config/counters table (id always = 1)."""
    __tablename__ = "app_config"

    id: int = Field(default=1, primary_key=True)
    serper_queries_used: int = 0


class ScrapeJob(SQLModel, table=True):
    __tablename__ = "scrape_job"

    id: Optional[int] = Field(default=None, primary_key=True)
    niche: str
    status: str = "running"          # running | done | failed
    total_found: int = 0
    new_contacts: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class Contact(SQLModel, table=True):
    __tablename__ = "contact"

    id: Optional[int] = Field(default=None, primary_key=True)
    website_url: str = Field(unique=True)   # normalised domain, always unique
    email: str
    email_type: str = "general"             # contact | advertise | marketing | general
    niche: str
    job_id: int = Field(foreign_key="scrape_job.id")
    is_accessible: bool = True
    has_ads: bool = False
    ads_txt_valid: bool = False
    traffic_guess: str = "low"              # low | medium | high
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    notes: str = ""
    contacted: bool = False                 # manual toggle, no email sent
