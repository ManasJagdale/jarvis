"""
nvidia_client.py

LLM client for NVIDIA NIM (build.nvidia.com) -- any catalog model that
supports OpenAI-format function calling. Uses the standard `openai`
Python package pointed at NVIDIA's endpoint, since NIM's API is
OpenAI-compatible; no NVIDIA-specific SDK needed.

This is a drop-in alternative to gemini_client.py: same LLMClient
interface (see llm_client.py), so jarvis_core.py doesn't know or care
which one is active. Switch providers by editing config.ACTIVE_PROVIDER,
not by touching main.py or gui.py -- see providers.py.

Install with:
    pip install openai
"""

import json

from openai import OpenAI

from config import NVIDIA_API_KEY, NVIDIA_MODEL
from llm_client import LLMClient, LLMResponse

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaClient(LLMClient):
    def __init__(self):
        if not NVIDIA_API_KEY:
            raise RuntimeError(
                "NVIDIA_API_KEY environment variable is not set.\n"
                "Get a free key at https://build.nvidia.com/settings/api-keys, "
                "then set it permanently:\n"
                '  setx NVIDIA_API_KEY "your-key-here"   (PowerShell, run once, '
                "then reopen your terminal)"
            )
        self.client = OpenAI(api_key=NVIDIA_API_KEY, base_url=NVIDIA_BASE_URL)

    def _build_tools(self, tool_schemas: list[dict]) -> list[dict] | None:
        """Convert our simple tool_schemas into OpenAI's tools format."""
        if not tool_schemas:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tool_schemas
        ]

    def _build_history(self, messages: list[dict]) -> list[dict]:
        """
        Convert our simple message list into OpenAI's chat message format.

        OpenAI-style tool calling needs each tool call to carry an `id`,
        and each tool result to reference it back via `tool_call_id`. Our
        internal message format (shared with gemini_client.py, which has
        no concept of call ids -- Gemini matches by name instead) doesn't
        store one. jarvis_core.run_turn() always appends the tool-result
        messages immediately after the assistant message that requested
        them, one per call, in the same order -- so we generate synthetic
        ids here and match purely by that guaranteed ordering, without
        needing to change jarvis_core.py at all.
        """
        result: list[dict] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg["role"]

            if role == "user":
                result.append({"role": "user", "content": msg["content"]})
                i += 1

            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    result.append({"role": "assistant", "content": msg.get("content", "")})
                    i += 1
                    continue

                call_ids = [f"call_{i}_{idx}" for idx in range(len(tool_calls))]
                result.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["args"]),
                                },
                            }
                            for call_id, call in zip(call_ids, tool_calls)
                        ],
                    }
                )
                i += 1

                # Consume the tool-result messages jarvis_core.py placed
                # right after this one, one per call, in order.
                for call_id in call_ids:
                    if i < len(messages) and messages[i]["role"] == "tool":
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": messages[i]["content"],
                            }
                        )
                        i += 1
                    else:
                        break  # shouldn't happen given jarvis_core's ordering guarantee

            else:
                # Orphan "tool" message with no preceding assistant
                # tool_calls entry -- shouldn't normally happen, skip
                # defensively rather than crash mid-conversation.
                i += 1

        return result

    def generate(
        self, messages: list[dict], tool_schemas: list[dict], system_prompt: str = ""
    ) -> LLMResponse:
        oi_messages = []
        if system_prompt:
            oi_messages.append({"role": "system", "content": system_prompt})
        oi_messages.extend(self._build_history(messages))

        tools = self._build_tools(tool_schemas)

        response = self.client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=oi_messages,
            tools=tools,
            tool_choice="auto" if tools else None,
        )

        message = response.choices[0].message
        text = message.content or ""

        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"name": tc.function.name, "args": args})

        return LLMResponse(text=text, tool_calls=tool_calls, raw=None)
