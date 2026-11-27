"""Live Gemini adapters using the official google-genai SDK."""

from typing import Any

from google import genai
from google.genai import types

from pr_agent.agents.runner import LLMTurnResponse, ToolCallRequest, load_prompt


class GeminiTriageLLM:
    """Implements TriageLLM using google-genai's async models API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client or genai.Client(api_key=api_key)

    async def classify(self, pr_title: str, pr_description: str, changed_files: list[str]) -> str:
        """Return a JSON string matching RouterDecision."""
        system_prompt = load_prompt("triage")
        user_text = (
            f"<pr_metadata>\ntitle: {pr_title}\ndescription: {pr_description}\n"
            f"changed_files: {changed_files}\n</pr_metadata>"
        )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.0,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_text,
            config=config,
        )
        if getattr(response, "text", None):
            return str(response.text)
        return '{"skip_review": false, "invoke": ["logic"], "reason": "Empty triage response"}'


class GeminiAgentLLM:
    """Implements AgentLLM with native function calling via google-genai."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._client = client or genai.Client(api_key=api_key)

    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        """Execute one Gemini turn and map function_call or JSON text to LLMTurnResponse."""
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages
        ]

        genai_tools = None
        if tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters_json_schema=t.get("parameters"),
                )
                for t in tools
            ]
            genai_tools = [types.Tool(function_declarations=declarations)]

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=genai_tools,
            temperature=0.1,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        tool_calls: list[ToolCallRequest] = []
        text_chunks: list[str] = []

        candidates = getattr(response, "candidates", None) or []
        parts = candidates[0].content.parts if candidates and candidates[0].content else []
        for part in parts or []:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                tool_calls.append(
                    ToolCallRequest(
                        tool=fc.name,
                        args=dict(fc.args or {}),
                    )
                )
            elif getattr(part, "text", None):
                text_chunks.append(str(part.text))

        if tool_calls:
            return LLMTurnResponse(tool_calls=tool_calls)

        raw_text = "\n".join(text_chunks).strip()
        return LLMTurnResponse(final_json=_strip_markdown_fences(raw_text) or None)


def _strip_markdown_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text
