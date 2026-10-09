"""A desktop notification, as plain text (never transcript content or speaker names)."""

from pydantic import BaseModel, ConfigDict


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    body: str = ""
