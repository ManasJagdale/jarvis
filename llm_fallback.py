"""
llm_fallback.py

Wraps GeminiClient (primary) and NvidiaClient (fallback) behind the single
LLMClient interface -- same swap-point pattern as gemini_client.py /
nvidia_client.py, so jarvis_core.py doesn't know or care that a fallback
is happening.

Behavior: every call tries Gemini first. If Gemini raises ANY exception
(quota, network, model errors, etc.), NVIDIA handles that turn instead,
and Gemini is skipped entirely for the next GEMINI_COOLDOWN_SECONDS -- so
a run of failing calls doesn't each pay Gemini's latency before falling
back. After the cooldown, Gemini is tried again on the next turn, and
only stays bypassed if it fails again.

on_diagnostic: optional callback(message: str), set by a caller that
wants these fallback/cooldown notices surfaced somewhere other than the
terminal (e.g. server.py forwards them to the browser over WebSocket).
Always still printed to the terminal too -- this is additive, not a
replacement. main.py and gui.py never set it, so they behave exactly as
before.
"""

import time
from typing import Callable, Optional

from config import GEMINI_COOLDOWN_SECONDS
from gemini_client import GeminiClient
from llm_client import LLMClient, LLMResponse
from nvidia_client import NvidiaClient


class FallbackLLMClient(LLMClient):
    def __init__(self):
        # Both built eagerly (not lazily on first use) so a missing API key
        # surfaces as a clear startup error, not a mid-conversation failure.
        self._gemini = GeminiClient()
        self._nvidia = NvidiaClient()
        self._cooldown_until = 0.0  # epoch seconds; 0 = never skip Gemini
        self.on_diagnostic: Optional[Callable[[str], None]] = None

    def _notify(self, message: str) -> None:
        print(message)
        if self.on_diagnostic:
            self.on_diagnostic(message)

    def generate(
        self, messages: list[dict], tool_schemas: list[dict], system_prompt: str = ""
    ) -> LLMResponse:
        now = time.time()

        if now < self._cooldown_until:
            return self._nvidia.generate(messages, tool_schemas, system_prompt)

        try:
            return self._gemini.generate(messages, tool_schemas, system_prompt)
        except Exception as exc:  # noqa: BLE001 - any Gemini failure triggers fallback
            mins = GEMINI_COOLDOWN_SECONDS // 60
            self._notify(
                f"⚠️  Gemini call failed ({exc}) -- falling back to NVIDIA for the next {mins} min."
            )
            self._cooldown_until = now + GEMINI_COOLDOWN_SECONDS
            return self._nvidia.generate(messages, tool_schemas, system_prompt)
