from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class PortalCredential(SQLModel, table=True):
    __tablename__ = "portalcredential"

    id: Optional[int] = Field(default=None, primary_key=True)
    applicant_id: int = Field(foreign_key="applicant.id")
    portal_domain: str          # e.g. "apply.ethz.ch"
    username: str
    password: str
    notes: str = ""             # e.g. "SSO, needs 2FA bypass"
    created_at: datetime = Field(default_factory=datetime.utcnow)
