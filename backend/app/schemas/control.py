from pydantic import BaseModel


class ToggleRequest(BaseModel):
    enabled: bool
