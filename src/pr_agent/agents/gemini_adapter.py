"""Live Gemini REST API adapters implementing TriageLLM and AgentLLM."""

from typing import Any

import httpx

from pr_agent.agents.runner import LLMTurnResponse, ToolCallRequest, load_prompt

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiTriageLLM:
    """Implements TriageLLM using Gemini's generateContent REST API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._http_client = http_client

    async def classify(self, pr_title: str, pr_description: str, changed_files: list[str]) -> str:
        """Return a JSON string matching RouterDecision."""
        system_prompt = load_prompt("triage")
        user_text = (
            f"<pr_metadata>\ntitle: {pr_title}\ndescription: {pr_description}\n"
            f"changed_files: {changed_files}\n</pr_metadata>"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
        }
        data = await _post_gemini(self._model, self._api_key, payload, self._http_client)
        parts = _extract_parts(data)
        for part in parts:
            if "text" in part:
                return str(part["text"])
        return '{"skip_review": false, "invoke": ["logic"], "reason": "Empty triage response"}'


class GeminiAgentLLM:
    """Implements AgentLLM with native function calling against Gemini REST API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-pro",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._http_client = http_client

    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        """Execute one Gemini turn and map functionCall or JSON text to LLMTurnResponse."""
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ]
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.1},
        }
        if tools:
            payload["tools"] = [{"functionDeclarations": tools}]

        data = await _post_gemini(self._model, self._api_key, payload, self._http_client)
        parts = _extract_parts(data)

        tool_calls: list[ToolCallRequest] = []
        text_chunks: list[str] = []
        for part in parts:
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    ToolCallRequest(
                        tool=fc["name"],
                        args=fc.get("args") or {},
                    )
                )
            elif "text" in part:
                text_chunks.append(str(part["text"]))

        if tool_calls:
            return LLMTurnResponse(tool_calls=tool_calls)

        raw_text = "\n".join(text_chunks).strip()
        cleaned_json = _strip_markdown_fences(raw_text)
        return LLMTurnResponse(final_json=cleaned_json or None)


async def _post_gemini(
    model: str,
    api_key: str,
    payload: dict[str, Any],
    http_client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    url = f"{_GEMINI_API_BASE}/{model}:generateContent"
    params = {"key": api_key}
    if http_client is not None:
        resp = await http_client.post(url, params=params, json=payload)
    else:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, params=params, json=payload)
    resp.raise_for_status()
    return resp.json()


def _extract_parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = data.get("candidates") or []
    if not candidates:
        return []
    content = candidates[0].get("content") or {}
    return list(content.get("parts") or [])


def _strip_markdown_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text
