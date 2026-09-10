import uuid

from pydantic import BaseModel


class SearchResultOut(BaseModel):
    model_config = {"from_attributes": True}

    chunk_id: uuid.UUID
    file_path: str
    language: str
    chunk_type: str
    symbol_name: str | None
    start_line: int
    end_line: int
    content: str
    score: float
