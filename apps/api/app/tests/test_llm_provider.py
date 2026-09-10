from unittest.mock import AsyncMock, MagicMock

import httpx
from groq import BadRequestError as GroqBadRequestError

from app.llm.provider import GeminiLLMProvider, GroqLLMProvider, Message, ToolCall, ToolSpec

WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get the weather",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _groq_tool_call_response(name: str, arguments: str) -> MagicMock:
    tool_call = MagicMock()
    tool_call.id = "call_abc"
    tool_call.function.name = name
    tool_call.function.arguments = arguments
    message = MagicMock()
    message.content = None
    message.tool_calls = [tool_call]
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


async def test_groq_parses_tool_call_from_response() -> None:
    provider = GroqLLMProvider(api_key="fake", model="openai/gpt-oss-120b")
    provider._client.chat.completions.create = AsyncMock(
        return_value=_groq_tool_call_response("get_weather", '{"city": "Paris"}')
    )

    result = await provider.complete(
        [Message(role="user", content="weather?")], tools=[WEATHER_TOOL], system="sys"
    )

    expected = ToolCall(id="call_abc", name="get_weather", arguments={"city": "Paris"})
    assert result.tool_calls == [expected]
    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert call_kwargs["tools"][0]["function"]["name"] == "get_weather"


async def test_groq_round_trips_a_tool_result_message() -> None:
    provider = GroqLLMProvider(api_key="fake", model="openai/gpt-oss-120b")
    final = MagicMock()
    final.choices = [MagicMock(message=MagicMock(content="It's sunny.", tool_calls=None))]
    provider._client.chat.completions.create = AsyncMock(return_value=final)

    messages = [
        Message(role="user", content="weather?"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="call_abc", name="get_weather", arguments={"city": "Paris"})],
        ),
        Message(role="tool", content="sunny, 72F", tool_call_id="call_abc", name="get_weather"),
    ]

    result = await provider.complete(messages, tools=[WEATHER_TOOL], system="sys")

    assert result.content == "It's sunny."
    api_messages = provider._client.chat.completions.create.call_args.kwargs["messages"]
    assert api_messages[-1] == {"role": "tool", "tool_call_id": "call_abc", "content": "sunny, 72F"}
    assert api_messages[-2]["tool_calls"][0]["id"] == "call_abc"
    assert api_messages[-2]["tool_calls"][0]["function"]["arguments"] == '{"city": "Paris"}'


async def test_groq_recovers_from_a_hallucinated_tool_call_schema() -> None:
    """Regression test for a real failure hit during development: gpt-oss-120b
    occasionally calls a tool with arguments that don't match its schema
    (e.g. a made-up `path`/`depth` instead of `query`). Groq validates this
    client-side and rejects the whole request with a 400 -- there's no
    tool_calls in that error to recover from, so this must degrade to a
    plain-text response the caller's retry loop already knows how to handle,
    not propagate as an unhandled exception that kills the whole plan."""
    provider = GroqLLMProvider(api_key="fake", model="openai/gpt-oss-120b")
    fake_response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.groq.com/x"), json={"error": {}}
    )
    provider._client.chat.completions.create = AsyncMock(
        side_effect=GroqBadRequestError(
            "Tool call validation failed: missing properties: 'query'",
            response=fake_response,
            body=None,
        )
    )

    result = await provider.complete(
        [Message(role="user", content="weather?")], tools=[WEATHER_TOOL], system="sys"
    )

    assert result.tool_calls == []
    assert result.content is not None
    assert "missing properties" in result.content


async def test_groq_sets_tool_choice_when_force_tool_is_given() -> None:
    provider = GroqLLMProvider(api_key="fake", model="openai/gpt-oss-120b")
    provider._client.chat.completions.create = AsyncMock(
        return_value=_groq_tool_call_response("submit_plan", "{}")
    )

    await provider.complete(
        [Message(role="user", content="go")],
        tools=[WEATHER_TOOL],
        system="sys",
        force_tool="submit_plan",
    )

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {"type": "function", "function": {"name": "submit_plan"}}


async def test_groq_leaves_tool_choice_unset_without_force_tool() -> None:
    provider = GroqLLMProvider(api_key="fake", model="openai/gpt-oss-120b")
    provider._client.chat.completions.create = AsyncMock(
        return_value=_groq_tool_call_response("get_weather", '{"city": "Paris"}')
    )

    await provider.complete(
        [Message(role="user", content="go")], tools=[WEATHER_TOOL], system="sys"
    )

    call_kwargs = provider._client.chat.completions.create.call_args.kwargs
    # Not merely None -- omitted entirely. Groq's client treats an explicit
    # tool_choice=None differently from the kwarg being absent (see the
    # regression this guards against, in provider.py).
    assert "tool_choice" not in call_kwargs


def _gemini_function_call_part(name: str, args: dict, thought_signature: bytes | None) -> MagicMock:
    part = MagicMock()
    # MagicMock(name=...) sets the mock's own repr, not a `.name` attribute
    # -- must be assigned separately to actually stub `function_call.name`.
    part.function_call = MagicMock()
    part.function_call.id = None
    part.function_call.name = name
    part.function_call.args = args
    part.text = None
    part.thought_signature = thought_signature
    return part


async def test_gemini_parses_tool_call_and_captures_thought_signature() -> None:
    provider = GeminiLLMProvider(api_key="fake", model="gemini-3.6-flash")
    part = _gemini_function_call_part(
        "get_weather", {"city": "Paris"}, thought_signature=b"\x01\x02"
    )
    response = MagicMock()
    response.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    provider._client.aio.models.generate_content = AsyncMock(return_value=response)

    result = await provider.complete(
        [Message(role="user", content="weather?")], tools=[WEATHER_TOOL], system="sys"
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "Paris"}
    assert result.tool_calls[0].thought_signature == b"\x01\x02"


async def test_gemini_replays_thought_signature_on_the_next_turn() -> None:
    """Regression test for the real bug hit during development: Gemini 3.x
    rejects a tool-result turn unless the preceding function_call part
    carries back the exact thought_signature from its own response."""
    provider = GeminiLLMProvider(api_key="fake", model="gemini-3.6-flash")
    final_part = MagicMock(function_call=None, text="Sunny.")
    final = MagicMock()
    final.candidates = [MagicMock(content=MagicMock(parts=[final_part]))]
    provider._client.aio.models.generate_content = AsyncMock(return_value=final)

    messages = [
        Message(role="user", content="weather?"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="get_weather",
                    arguments={"city": "Paris"},
                    thought_signature=b"\xab\xcd",
                )
            ],
        ),
        Message(role="tool", content="sunny", tool_call_id="call_1", name="get_weather"),
    ]

    await provider.complete(messages, tools=[WEATHER_TOOL], system="sys")

    contents = provider._client.aio.models.generate_content.call_args.kwargs["contents"]
    model_turn = contents[1]
    assert model_turn.role == "model"
    assert model_turn.parts[0].function_call.name == "get_weather"
    assert model_turn.parts[0].thought_signature == b"\xab\xcd"


async def test_gemini_sets_tool_config_when_force_tool_is_given() -> None:
    provider = GeminiLLMProvider(api_key="fake", model="gemini-3.6-flash")
    part = _gemini_function_call_part("submit_plan", {}, thought_signature=None)
    response = MagicMock()
    response.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    provider._client.aio.models.generate_content = AsyncMock(return_value=response)

    await provider.complete(
        [Message(role="user", content="go")],
        tools=[WEATHER_TOOL],
        system="sys",
        force_tool="submit_plan",
    )

    config = provider._client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.tool_config.function_calling_config.mode == "ANY"
    assert config.tool_config.function_calling_config.allowed_function_names == ["submit_plan"]


async def test_gemini_leaves_tool_config_unset_without_force_tool() -> None:
    provider = GeminiLLMProvider(api_key="fake", model="gemini-3.6-flash")
    part = _gemini_function_call_part("get_weather", {"city": "Paris"}, thought_signature=None)
    response = MagicMock()
    response.candidates = [MagicMock(content=MagicMock(parts=[part]))]
    provider._client.aio.models.generate_content = AsyncMock(return_value=response)

    await provider.complete(
        [Message(role="user", content="go")], tools=[WEATHER_TOOL], system="sys"
    )

    config = provider._client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.tool_config is None
