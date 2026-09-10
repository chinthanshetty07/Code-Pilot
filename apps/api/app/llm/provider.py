import json
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from google import genai
from google.genai import types as genai_types
from groq import AsyncGroq
from groq import BadRequestError as GroqBadRequestError

from app.core.config import get_settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Gemini-only: its 3.x models require round-tripping this opaque value
    # on a function call part for a later turn to be accepted at all (see
    # GeminiLLMProvider) -- it has no meaning for Groq/OpenAI-shaped calls.
    thought_signature: bytes | None = None


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Only set on role="tool" messages (a tool's result being fed back in):
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema, e.g. {"type": "object", "properties": {...}}


@dataclass
class TokenUsage:
    """Real token counts from the provider's own response, for Milestone
    11's cost tracking (see app/services/usage.py). For Gemini,
    output_tokens already includes thinking/reasoning tokens (Gemini
    bills them at the output rate -- see estimate_cost_usd's pricing
    table comment) -- callers never need to add those separately."""

    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    # None only for a response that never reached the provider at all
    # (e.g. GroqLLMProvider's own tool-call-rejected recovery path below,
    # which never made a real request) -- a genuine API response always
    # carries usage for both providers in practice.
    usage: TokenUsage | None = None


class LLMProvider(Protocol):
    @property
    def model(self) -> str:
        """The exact model name this provider is configured for -- needed
        alongside a response's TokenUsage to price it correctly (see
        app/services/usage.py's per-(provider, model) pricing table)."""
        ...

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str,
        force_tool: str | None = None,
    ) -> LLMResponse:
        """`force_tool`, when set, compels the model to call that exact tool
        this turn rather than leaving the choice to it -- used to guarantee
        convergence (e.g. forcing submit_plan on an agent loop's last turn
        instead of risking it search forever and never conclude)."""
        ...


class GroqLLMProvider:
    """OpenAI-compatible chat-completions format -- Groq's API is designed
    as a drop-in for the OpenAI wire format, so this is a near-direct
    translation rather than a real adaptation."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def _to_api_messages(self, messages: list[Message], system: str) -> list[dict]:
        api_messages: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "tool":
                api_messages.append(
                    {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
                )
            elif m.tool_calls:
                api_messages.append(
                    {
                        "role": "assistant",
                        "content": m.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                api_messages.append({"role": m.role, "content": m.content or ""})
        return api_messages

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str,
        force_tool: str | None = None,
    ) -> LLMResponse:
        api_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
        # Groq's client distinguishes "omitted" from "explicitly None" for
        # optional params -- passing tool_choice=None (rather than leaving
        # it out of the call entirely) sends a literal null the API then
        # rejects with a 400, "Only allowed string values ... [none, auto,
        # required]". Every unforced turn was hitting exactly that until
        # this was built as a conditionally-populated kwargs dict instead.
        extra_kwargs: dict = {}
        if api_tools:
            extra_kwargs["tools"] = api_tools
        if force_tool:
            extra_kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=self._to_api_messages(messages, system),
                **extra_kwargs,
            )
        except GroqBadRequestError as exc:
            # Observed in practice: gpt-oss-120b occasionally hallucinates
            # tool arguments that don't match the declared schema (e.g. a
            # made-up `path`/`depth` for search_code, which only takes
            # `query`). Groq validates client-side and rejects the whole
            # request with a 400 rather than returning a normal response for
            # the caller to inspect -- so there's no tool_calls to recover
            # from here. Surfacing it as if the model had answered in plain
            # text (no tool_calls) lets the existing "nudge it back on
            # track" loop in the caller retry, instead of this hard-failing
            # the entire plan over one bad tool call.
            return LLMResponse(
                content=f"(tool call rejected: {exc})",
                tool_calls=[],
            )
        msg = response.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments))
            for tc in (msg.tool_calls or [])
        ]
        usage = (
            TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
            if response.usage is not None
            else None
        )
        return LLMResponse(content=msg.content, tool_calls=tool_calls, usage=usage)


class GeminiLLMProvider:
    """Gemini's `contents` format differs from OpenAI's `messages` in two
    ways that matter here: there's no "tool" role (a tool result is a
    `function_response` part on a "user"-role turn), and function calls
    carry no id -- Gemini matches a response to a call by name/position
    within the turn, not an explicit id, so `ToolCall.id` is synthesized
    here purely for this codebase's own bookkeeping."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def _to_contents(self, messages: list[Message]) -> list[genai_types.Content]:
        contents: list[genai_types.Content] = []
        for m in messages:
            if m.role == "tool":
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part.from_function_response(
                                name=m.name or "", response={"result": m.content or ""}
                            )
                        ],
                    )
                )
            elif m.tool_calls:
                parts = [
                    genai_types.Part(
                        function_call=genai_types.FunctionCall(name=tc.name, args=tc.arguments),
                        thought_signature=tc.thought_signature,
                    )
                    for tc in m.tool_calls
                ]
                if m.content:
                    parts.insert(0, genai_types.Part.from_text(text=m.content))
                contents.append(genai_types.Content(role="model", parts=parts))
            else:
                role = "model" if m.role == "assistant" else "user"
                contents.append(
                    genai_types.Content(
                        role=role, parts=[genai_types.Part.from_text(text=m.content or "")]
                    )
                )
        return contents

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str,
        force_tool: str | None = None,
    ) -> LLMResponse:
        genai_tools = (
            [
                genai_types.Tool(
                    function_declarations=[
                        genai_types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters_json_schema=t.parameters,
                        )
                        for t in tools
                    ]
                )
            ]
            if tools
            else None
        )
        tool_config = (
            genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=[force_tool]
                )
            )
            if force_tool
            else None
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=self._to_contents(messages),
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                tools=genai_tools,
                tool_config=tool_config,
                # Handled by this codebase's own agent loop uniformly across
                # providers, rather than relying on Gemini-specific
                # auto-execution that Groq has no equivalent for.
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        candidate = response.candidates[0]
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in candidate.content.parts:
            if part.function_call:
                tool_calls.append(
                    ToolCall(
                        id=part.function_call.id or f"call_{uuid.uuid4().hex[:8]}",
                        name=part.function_call.name or "",
                        arguments=dict(part.function_call.args or {}),
                        thought_signature=part.thought_signature,
                    )
                )
            elif part.text:
                text_parts.append(part.text)

        usage = None
        if response.usage_metadata is not None:
            meta = response.usage_metadata
            # thoughts_token_count (Gemini 3.x's reasoning tokens) is
            # billed at the output rate, not tracked separately -- see
            # TokenUsage's docstring and estimate_cost_usd's pricing table
            # comment (app/services/usage.py).
            usage = TokenUsage(
                input_tokens=meta.prompt_token_count or 0,
                output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
            )
        return LLMResponse(content="".join(text_parts) or None, tool_calls=tool_calls, usage=usage)


def get_llm_provider(provider_name: str) -> LLMProvider:
    """`provider_name` is a provider ("groq" | "gemini"), not an agent role.
    Callers that route by role (e.g. the Planner using whichever provider
    `settings.planner_llm_provider` names) read that setting themselves and
    pass the resulting name in here -- this module doesn't know what a
    "planner" is, the same way `app.rag.embeddings` doesn't know about
    "indexing" vs. "search"."""
    settings = get_settings()
    if provider_name == "groq":
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not configured")
        return GroqLLMProvider(settings.groq_api_key, settings.groq_model)
    if provider_name == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not configured")
        return GeminiLLMProvider(settings.gemini_api_key, settings.gemini_model)
    raise ValueError(f"Unsupported LLM provider: {provider_name}")
