"""
gemini_client.py

The ONLY file that knows anything about Gemini specifically. It translates
between our provider-agnostic LLMResponse/messages format and the Gemini
Python SDK's format.

Uses `google-genai` — the current, actively-maintained Google SDK (the
older `google-generativeai` package was fully deprecated and stopped
receiving updates).

If you switch to Claude later, you write claude_client.py implementing the
same LLMClient interface, then change one import in main.py. Nothing else
in the codebase needs to change.

Install with:
    pip install google-genai
"""

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL
from llm_client import LLMClient, LLMResponse


class GeminiClient(LLMClient):
    def __init__(self):
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY environment variable is not set.\n"
                "Get a free key at https://ai.google.dev / Google AI Studio, "
                "then set it permanently:\n"
                '  setx GEMINI_API_KEY "your-key-here"   (PowerShell, run once, '
                "then reopen your terminal)"
            )
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def _build_tools(self, tool_schemas: list[dict]) -> list[types.Tool] | None:
        """Convert our simple tool_schemas into Gemini's Tool/FunctionDeclaration format."""
        if not tool_schemas:
            return None
        declarations = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
            )
            for t in tool_schemas
        ]
        return [types.Tool(function_declarations=declarations)]

    def _build_history(self, messages: list[dict]) -> list[types.Content]:
        """
        Convert our simple message list into Gemini's Content list.

        Gemini only has two roles: "user" and "model". A model turn that
        requested tool calls carries function_call parts; the results we
        feed back go in a following "user" turn as function_response parts.
        This is why main.py stores each tool_call alongside the assistant
        message that requested it -- Gemini needs to see its own request
        echoed back, not just the result, to keep the conversation coherent.
        """
        contents = []
        for msg in messages:
            role = msg["role"]

            if role == "user":
                contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=msg["content"])])
                )

            elif role == "assistant":
                if msg.get("raw") is not None:
                    # Replay Gemini's own response content verbatim so any
                    # thought_signature it attached survives the round trip.
                    # Reconstructing parts from scratch (name+args only)
                    # drops that signature and Gemini 3.x rejects the call.
                    contents.append(msg["raw"])
                else:
                    parts = []
                    if msg.get("content"):
                        parts.append(types.Part.from_text(text=msg["content"]))
                    for call in msg.get("tool_calls", []):
                        parts.append(
                            types.Part.from_function_call(name=call["name"], args=call["args"])
                        )
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=msg["name"], response={"result": msg["content"]}
                            )
                        ],
                    )
                )
        return contents

    def generate(
        self, messages: list[dict], tool_schemas: list[dict], system_prompt: str = ""
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            tools=self._build_tools(tool_schemas),
            system_instruction=system_prompt or None,
        )
        contents = self._build_history(messages)

        response = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )

        text = response.text or ""
        tool_calls = [
            {"name": fc.name, "args": dict(fc.args)} for fc in (response.function_calls or [])
        ]

        return LLMResponse(
            text=text, tool_calls=tool_calls, raw=response.candidates[0].content
        )