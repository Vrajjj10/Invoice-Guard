"""Provider-agnostic tool-calling chat interface + Gemini implementation."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from google.genai import types

from app.llm.client import _get_client


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema object


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass
class ToolResult:
    call: ToolCall
    output: dict[str, Any]


@dataclass
class ChatTurn:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0


class ChatSession(Protocol):
    def send(self, message: str | list[ToolResult]) -> ChatTurn: ...


class ToolChatProvider(Protocol):
    def start(self, system: str, tools: list[ToolSpec]) -> ChatSession: ...


class GeminiSession:
    """Keeps the Gemini `contents` history across turns."""

    def __init__(self, model: str, system: str, tools: list[ToolSpec]):
        self.model = model
        self.contents: list[types.Content] = []
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters_json_schema=t.parameters
                )
                for t in tools
            ])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0,
            max_output_tokens=1024,
        )

    def send(self, message: str | list[ToolResult]) -> ChatTurn:
        if isinstance(message, str):
            parts = [types.Part.from_text(text=message)]
        else:
            parts = [
                types.Part(function_response=types.FunctionResponse(
                    id=r.call.id, name=r.call.name, response=r.output
                ))
                for r in message
            ]
        self.contents.append(types.Content(role="user", parts=parts))
        resp = _get_client().models.generate_content(
            model=self.model, contents=self.contents, config=self.config
        )
        if resp.candidates and resp.candidates[0].content:
            self.contents.append(resp.candidates[0].content)  # keeps thought signatures
        calls = [ToolCall(fc.name, dict(fc.args or {}), fc.id) for fc in resp.function_calls or []]
        text = "" if calls else (resp.text or "")
        tokens = resp.usage_metadata.total_token_count if resp.usage_metadata else 0
        return ChatTurn(text=text, calls=calls, tokens=tokens or 0)


class GeminiProvider:
    def __init__(self, model: str):
        self.model = model

    def start(self, system: str, tools: list[ToolSpec]) -> ChatSession:
        return GeminiSession(self.model, system, tools)
