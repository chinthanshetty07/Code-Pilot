import uuid

from pydantic import BaseModel


class AuthUserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    username: str
    name: str | None
    avatar_url: str | None
