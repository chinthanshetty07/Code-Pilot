from pydantic import BaseModel


class AgentUsageOut(BaseModel):
    # Serialized from app.services.usage.AgentUsage, a plain dataclass, not
    # an ORM model -- from_attributes is what lets Pydantic read it via
    # attribute access either way, same as every ORM-backed *Out schema
    # elsewhere in this project.
    model_config = {"from_attributes": True}

    # agent is "planner" | "coder" | "reviewer" -- see app/services/usage.py
    agent: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class UsageSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    by_agent: list[AgentUsageOut]
